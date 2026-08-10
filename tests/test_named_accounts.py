from __future__ import annotations

from datetime import date
import re

from app.db import SessionLocal
from app.auth import hash_password
from app.models import Evaluator, EvaluatorDirectory, Journey, UserAccount


def _login(client, username="JP Chaaya", password="test-password"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def test_named_permissions_protect_admin_results_and_attendance(client):
    owner = _login(client)
    assert owner["evaluatorRole"] == "dossard"
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    journey = client.post("/api/admin/journeys", headers=headers,
                          json={"name": "Permissions", "event_date": "2026-09-09"}).json()
    recruit = client.post(
        f"/api/admin/journeys/{journey['id']}/recruits",
        headers=headers,
        json={"name": "General Assessment Recruit"},
    ).json()
    created = client.post("/api/auth/accounts", headers=headers, json={
        "username": "Permission Tester", "password": "temporary-password",
        "evaluator_role": "dossard",
        "full_name": "Test Person Full Name",
        "phone_number": "70123456",
    })
    assert created.status_code == 200, created.text
    account = created.json()
    assert "managedPassword" not in account
    owner_accounts = client.get("/api/auth/accounts").json()
    visible = next(item for item in owner_accounts if item["username"] == "Permission Tester")
    assert visible["managedPassword"] == "temporary-password"
    assert visible["fullName"] == "Test Person Full Name"
    assert visible["phoneNumber"] == "70123456"
    picker_account = next(
        item for item in client.get("/api/auth/usernames").json()
        if item["username"] == "Permission Tester"
    )
    assert picker_account["fullName"] == "Test Person Full Name"
    assert "phoneNumber" not in picker_account
    updated = client.patch(f"/api/auth/accounts/{account['id']}", headers=headers, json={
        "can_results": True,
        "attendance_journey_ids": [journey["id"]],
        "base_version": account["version"],
    })
    assert updated.status_code == 200, updated.text

    user = _login(client, "Permission Tester", "temporary-password")
    assert client.get("/api/admin/journeys").status_code == 403
    assert client.get("/api/view/journeys").status_code == 200
    detail = client.get(f"/api/view/journeys/{journey['id']}")
    assert detail.status_code == 200
    profile_url = f"/api/view/journeys/{journey['id']}/recruits/{recruit['id']}/profile"
    profile = client.get(profile_url).json()
    saved = client.put(profile_url, headers={"X-CSRF-Token": user["csrfToken"]}, json={
        "punctuality": 0.7,
        "respect": 0.8,
        "seriousness": 0.9,
        "comment": "Results-team assessment",
        "notes": "Results-team note",
        "base_version": profile["assessment"]["version"],
    })
    assert saved.status_code == 200, saved.text
    updated_profile = client.get(profile_url).json()
    assert updated_profile["assessment"] == {
        "punctuality": 0.7,
        "respect": 0.8,
        "seriousness": 0.9,
        "comment": "Results-team assessment",
        "notes": "Results-team note",
        "version": 1,
    }
    assert updated_profile["history"][0]["actorName"] == "Permission Tester"
    stale = client.put(profile_url, headers={"X-CSRF-Token": user["csrfToken"]}, json={
        "punctuality": 1,
        "base_version": 0,
    })
    assert stale.status_code == 409

    owner = _login(client)
    journey_detail = client.get(f"/api/admin/journeys/{journey['id']}").json()
    token = journey_detail["recruitAttendancePath"].rsplit("/", 1)[-1]
    user = _login(client, "Permission Tester", "temporary-password")
    selected = client.post(f"/api/public/recruit-attendance/{token}/select",
                           headers={"X-CSRF-Token": user["csrfToken"]})
    assert selected.status_code == 200, selected.text


def test_marita_is_not_available_in_the_global_account_directory(client):
    names = [item["username"].casefold() for item in client.get("/api/auth/usernames").json()]
    assert "marita" not in names


def test_owner_is_always_a_dossard_in_permissions(client):
    owner = _login(client)
    accounts = client.get("/api/auth/accounts").json()
    jp = next(item for item in accounts if item["username"] == "JP Chaaya")
    assert jp["evaluatorRole"] == "dossard"

    changed = client.patch(
        f"/api/auth/accounts/{jp['id']}",
        headers={"X-CSRF-Token": owner["csrfToken"]},
        json={"evaluator_role": "overall", "base_version": jp["version"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["evaluatorRole"] == "dossard"


def test_owner_can_edit_and_safely_delete_account(client):
    owner = _login(client)
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    created = client.post("/api/auth/accounts", headers=headers, json={
        "username": "Editable Nickname",
        "password": "temporary-password",
        "evaluator_role": "dossard",
        "full_name": "Editable Full Name",
        "phone_number": "70111222",
    }).json()
    updated = client.patch(f"/api/auth/accounts/{created['id']}", headers=headers, json={
        "username": "Updated Nickname",
        "full_name": "Updated Full Name",
        "phone_number": "70999888",
        "evaluator_role": "overall",
        "base_version": created["version"],
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["username"] == "Updated Nickname"
    assert updated.json()["fullName"] == "Updated Full Name"
    assert updated.json()["phoneNumber"] == "70999888"
    assert _login(client, "Updated Nickname", "temporary-password")["evaluatorRole"] == "overall"

    owner = _login(client)
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    with SessionLocal() as db:
        account = db.get(UserAccount, created["id"])
        journey = Journey(
            name="Active deletion guard",
            event_date=date(2026, 9, 10),
            status="active",
            public_token="active-deletion-guard-token",
        )
        db.add(journey)
        db.flush()
        evaluator = Evaluator(
            journey_id=journey.id,
            directory_id=account.directory_id,
            name=account.username,
            role=account.evaluator_role,
            present=True,
            active=True,
        )
        db.add(evaluator)
        db.commit()
        evaluator_id = evaluator.id
        directory_id = account.directory_id

    blocked = client.delete(f"/api/auth/accounts/{created['id']}", headers=headers)
    assert blocked.status_code == 409
    with SessionLocal() as db:
        evaluator = db.get(Evaluator, evaluator_id)
        evaluator.present = False
        db.commit()

    deleted = client.delete(f"/api/auth/accounts/{created['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert client.post("/api/auth/login", json={
        "username": "Updated Nickname", "password": "temporary-password",
    }).status_code in {401, 403}
    with SessionLocal() as db:
        assert db.get(UserAccount, created["id"]) is None
        assert db.get(EvaluatorDirectory, directory_id).active is False
        assert db.get(Evaluator, evaluator_id) is not None

    owner_account = next(item for item in client.get("/api/auth/accounts").json() if item["isOwner"])
    assert client.delete(f"/api/auth/accounts/{owner_account['id']}", headers=headers).status_code == 409


def test_missing_accounts_are_generated_in_short_repeatable_batches(client):
    with SessionLocal() as db:
        db.add_all([
            EvaluatorDirectory(name=f"Batch Evaluator {index:02}", default_role="overall")
            for index in range(11)
        ])
        db.commit()

    owner = _login(client)
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    first = client.post("/api/auth/accounts/generate-missing", headers=headers)
    assert first.status_code == 200, first.text
    assert len(first.json()["created"]) == 8
    assert first.json()["remaining"] == 3
    assert all(re.fullmatch(r"batchevaluator\d{2}\d{3}", item["password"]) for item in first.json()["created"])

    second = client.post("/api/auth/accounts/generate-missing", headers=headers)
    assert second.status_code == 200, second.text
    assert len(second.json()["created"]) == 3
    assert second.json()["remaining"] == 0

    finished = client.post("/api/auth/accounts/generate-missing", headers=headers)
    assert finished.status_code == 200, finished.text
    assert finished.json() == {"created": [], "remaining": 0, "shownOnce": True}

    accounts = client.get("/api/auth/accounts").json()
    generated = [item for item in accounts if item["username"].startswith("Batch Evaluator")]
    assert len(generated) == 11
    assert all(item["managedPassword"] for item in generated)


def test_generator_replaces_an_existing_unrecoverable_password(client):
    with SessionLocal() as db:
        directory = EvaluatorDirectory(name="Legacy Evaluator", default_role="dossard")
        db.add(directory)
        db.flush()
        db.add(UserAccount(
            username=directory.name,
            password_hash=hash_password("old-unrecoverable-password"),
            managed_password=None,
            directory_id=directory.id,
            evaluator_role="dossard",
        ))
        db.commit()

    owner = _login(client)
    generated = client.post(
        "/api/auth/accounts/generate-missing",
        headers={"X-CSRF-Token": owner["csrfToken"]},
    ).json()
    credential = next(item for item in generated["created"] if item["username"] == "Legacy Evaluator")
    assert re.fullmatch(r"legacyevaluator\d{3}", credential["password"])
    assert _login(client, "Legacy Evaluator", credential["password"])["evaluatorRole"] == "dossard"
