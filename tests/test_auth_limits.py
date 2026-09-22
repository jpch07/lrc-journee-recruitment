from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import threading
import time
from types import SimpleNamespace

from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request
import pytest

from app import auth


def request(address="203.0.113.10"):
    return Request({"type": "http", "client": (address, 1234), "headers": []})


def attempt(username, *, workspace="event-a", address="203.0.113.10"):
    auth.enforce_login_rate_limit(request(address), username=username, workspace=workspace)


def test_thirty_distinct_accounts_can_log_in_together_behind_one_nat():
    with ThreadPoolExecutor(max_workers=30) as pool:
        list(pool.map(lambda number: attempt(f"Evaluator {number}"), range(30)))
    assert len(auth._login_attempts) == 30
    assert len(auth._login_ip_attempts["203.0.113.10"]) == 30


def test_repeated_invalid_account_is_limited_despite_case_or_spacing_changes():
    for _ in range(10):
        attempt("  Ａssessor   One  ")
    with pytest.raises(HTTPException) as caught:
        attempt("assessor one")
    assert caught.value.status_code == 429
    assert caught.value.headers["Retry-After"] == "300"
    # The same name in another workspace is a different account.
    attempt("assessor one", workspace="event-b")


def test_success_never_clears_another_accounts_failures_or_aggregate_budget():
    for _ in range(10):
        attempt("attacked account")
    attempt("legitimate account")
    auth.clear_login_attempts(request(), username="legitimate account", workspace="event-a")
    with pytest.raises(HTTPException):
        attempt("attacked account")
    assert len(auth._login_ip_attempts["203.0.113.10"]) == 11
    assert ("203.0.113.10", "event-a", "legitimate account") not in auth._login_attempts


def test_success_clears_only_its_matching_workspace_identity():
    attempt("same user", workspace="event-a")
    attempt("same user", workspace="event-b")
    auth.clear_login_attempts(request(), username="Same User", workspace="event-a")
    assert ("203.0.113.10", "event-a", "same user") not in auth._login_attempts
    assert ("203.0.113.10", "event-b", "same user") in auth._login_attempts


def test_aggregate_budget_is_bounded_even_with_successful_or_random_accounts():
    for index in range(100):
        name = f"user {index}"
        attempt(name)
        auth.clear_login_attempts(request(), username=name, workspace="event-a")
    with pytest.raises(HTTPException):
        attempt("new account")
    attempt("new account", address="203.0.113.11")


def test_concurrent_invalid_attempts_cannot_race_the_account_limit():
    def try_login(_):
        try:
            attempt("same target")
            return True
        except HTTPException:
            return False

    with ThreadPoolExecutor(max_workers=30) as pool:
        assert sum(pool.map(try_login, range(30))) == 10


def test_expired_buckets_are_removed_and_attempts_allowed_again(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(auth, "_login_last_pruned", 0.0)
    for _ in range(10):
        attempt("account")
    now[0] += 301
    attempt("new account", address="203.0.113.11")
    assert len(auth._login_attempts) == 1
    assert set(auth._login_ip_attempts) == {"203.0.113.11"}


def test_unique_attacker_identities_cannot_grow_memory_without_bound(monkeypatch):
    monkeypatch.setattr(auth, "LOGIN_MAX_BUCKETS", 4)
    for index in range(3):
        attempt(f"account {index}")
    with pytest.raises(HTTPException):
        attempt("account 4")
    assert len(auth._login_attempts) + len(auth._login_ip_attempts) == 4


def test_password_hashing_verification_and_legacy_admin_share_two_worker_limit(monkeypatch):
    counts = {"active": 0, "maximum": 0, "completed": 0}
    lock = threading.Lock()

    def work(result):
        with lock:
            counts["active"] += 1
            counts["maximum"] = max(counts["maximum"], counts["active"])
        time.sleep(0.02)
        with lock:
            counts["active"] -= 1
            counts["completed"] += 1
        return result

    class Hasher:
        def hash(self, _password):
            return work("hashed")

        def verify(self, _hash, _password):
            return work(True)

    monkeypatch.setattr(auth, "_password_hasher", Hasher())
    monkeypatch.setattr(auth, "settings", replace(auth.settings, admin_password_hash="stored"))

    def operation(index):
        if index % 3 == 0:
            return auth.hash_password("test")
        if index % 3 == 1:
            return auth.verify_password("stored", "test")
        return auth.verify_admin_password("test")

    with ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(operation, range(30)))
    assert all(results)
    assert counts == {"active": 0, "maximum": 2, "completed": 30}


def test_password_worker_is_released_after_mismatch(monkeypatch):
    class Hasher:
        def verify(self, _hash, _password):
            raise VerifyMismatchError("invalid password")

    monkeypatch.setattr(auth, "_password_hasher", Hasher())
    for _ in range(5):
        assert auth.verify_password("stored", "wrong") is False


def test_named_login_keeps_other_failed_username_limited(client):
    for _ in range(10):
        response = client.post("/api/auth/login", json={"username": "Not A Real User", "password": "wrong"})
        assert response.status_code == 403
    assert client.post("/api/auth/login", json={"username": "JP Chaaya", "password": "test-password"}).status_code == 200
    response = client.post("/api/auth/login", json={"username": "Not A Real User", "password": "wrong"})
    assert response.status_code == 429


def test_login_burst_performs_only_one_expired_session_cleanup_batch(monkeypatch):
    monkeypatch.setattr(auth, "_session_cleanup_last_attempt", None)
    calls = []
    lock = threading.Lock()

    class Database:
        def execute(self, statement):
            with lock:
                calls.append(statement)
            time.sleep(0.001)

    with ThreadPoolExecutor(max_workers=33) as pool:
        list(pool.map(lambda _: auth.clear_expired_sessions(Database()), range(33)))
    assert len(calls) == 6


def test_expired_session_cleanup_retries_after_interval_even_after_failed_attempt(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(auth, "_session_cleanup_last_attempt", None)
    calls = []

    class Database:
        def execute(self, statement):
            calls.append(statement)
            if len(calls) == 1:
                raise RuntimeError("Transaction rolled back")

    with pytest.raises(RuntimeError):
        auth.clear_expired_sessions(Database())
    auth.clear_expired_sessions(Database())
    assert len(calls) == 1
    now[0] += 61
    auth.clear_expired_sessions(Database())
    assert len(calls) == 7


@pytest.mark.parametrize(("cookie", "require"), [
    (auth.USER_COOKIE, auth.require_user),
    (auth.PLATFORM_COOKIE, auth.require_platform),
    (auth.ADMIN_COOKIE, auth.require_admin),
    (auth.EVALUATOR_COOKIE, auth.require_evaluator),
    (auth.RECRUIT_ATTENDANCE_COOKIE, auth.require_recruit_attendance),
])
def test_expired_sessions_are_rejected_even_when_physical_cleanup_is_throttled(monkeypatch, cookie, require):
    monkeypatch.setattr(auth, "settings", replace(auth.settings, environment="development"))
    monkeypatch.setattr(auth, "_session_cleanup_last_attempt", time.monotonic())
    expired = SimpleNamespace(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))

    class Database:
        def execute(self, _):
            raise AssertionError("Housekeeping should be throttled")

        def get(self, *_):
            return expired

    auth.clear_expired_sessions(Database())
    req = Request({"type": "http", "headers": [(b"cookie", f"{cookie}=expired-token".encode())]})
    with pytest.raises(HTTPException) as caught:
        require(req, Database())
    assert caught.value.status_code == 401
