from __future__ import annotations

import io
from pathlib import Path
import re
import smtplib
import ssl

import pytest

from scripts import event_day_guard


@pytest.fixture(autouse=True)
def clean_database():
    # These are pure monitoring tests; do not tear down a shared application DB.
    yield


@pytest.fixture(autouse=True)
def no_real_email(monkeypatch):
    for name in ("EVALDAY_SMTP_HOST", "EVALDAY_SMTP_PORT", "EVALDAY_SMTP_USERNAME", "EVALDAY_SMTP_PASSWORD", "EVALDAY_ALERT_FROM", "EVALDAY_ALERT_TO"):
        monkeypatch.delenv(name, raising=False)


def test_health_urls_are_normalized() -> None:
    assert event_day_guard.health_urls("https://example.test/") == (
        "https://example.test/health/live",
        "https://example.test/health/ready",
    )


def test_check_cycle_retries_failed_endpoint(monkeypatch) -> None:
    results = iter(
        [
            event_day_guard.ProbeResult(False, 503, "not ready"),
            event_day_guard.ProbeResult(True, 200, "ok"),
            event_day_guard.ProbeResult(True, 200, "ready"),
        ]
    )
    monkeypatch.setattr(event_day_guard, "probe", lambda *_args, **_kwargs: next(results))
    monkeypatch.setattr(event_day_guard.time, "sleep", lambda _seconds: None)

    assert event_day_guard.check_cycle(
        "https://example.test",
        retry_count=2,
        retry_delay_seconds=0,
        timeout_seconds=1,
    ) is True


def test_guard_continues_after_failure_and_reports_it(monkeypatch) -> None:
    outcomes = iter([False, True])
    monkeypatch.setattr(event_day_guard, "check_cycle", lambda *_args, **_kwargs: next(outcomes))
    monkeypatch.setattr(event_day_guard.time, "sleep", lambda _seconds: None)

    result = event_day_guard.run_guard(
        "https://example.test",
        duration_minutes=None,
        cycles=2,
        interval_seconds=1,
        initial_delay_seconds=0,
        retry_count=1,
        retry_delay_seconds=0,
        timeout_seconds=1,
    )

    assert result == 1


@pytest.mark.parametrize(("endpoint", "body", "expected"), [
    ("live", b'{"status":"ok","databaseReady":false}', True),
    ("ready", b'{"status":"ready"}', True),
    ("ready", b'<html>Starting your server...</html>', False),
    ("ready", b'{"status":"ok"}', False),
    ("ready", b'{"ready":true}', False),
    ("ready", b'null', False),
    ("live", b'[]', False),
    ("ready", b'{"status":"ready"' + b' ' * 8200 + b'}', False),
])
def test_probe_requires_actual_application_health_json(monkeypatch, endpoint, body, expected) -> None:
    response = io.BytesIO(body)
    response.status = 200
    monkeypatch.setattr(event_day_guard, "urlopen", lambda *_args, **_kwargs: response)
    assert event_day_guard.probe(f"https://example.test/health/{endpoint}").ok is expected


def test_probe_does_not_echo_sensitive_response_details(monkeypatch) -> None:
    response = io.BytesIO(b'{"status":"error","detail":"secret password here"}')
    response.status = 200
    monkeypatch.setattr(event_day_guard, "urlopen", lambda *_args, **_kwargs: response)
    result = event_day_guard.probe("https://example.test/health/ready")
    assert result.ok is False
    assert "secret password" not in result.detail


def test_continuous_watchdog_segments_cannot_start_from_scheduled_baseline() -> None:
    workflow = (Path(__file__).parents[1] / ".github/workflows/event-day-watchdog.yml").read_text(encoding="utf-8")
    segments = re.findall(r"^  ((?:primary|secondary)_[1-4]):\n(.*?)(?=^  \w+:|\Z)", workflow, re.M | re.S)
    assert len(segments) == 8
    for name, body in segments:
        condition = re.search(r"^    if: (.+)$", body, re.M)
        assert condition is not None, name
        assert "github.event_name == 'workflow_dispatch'" in condition.group(1), name
        if name.endswith(("_3", "_4")):
            assert "inputs.duration_hours == '12'" in condition.group(1), name
        if name.startswith("secondary"):
            assert "--no-email" in body


def configuration(port=587):
    return event_day_guard.EmailSettings("smtp.example.test", port, "user", "super-secret", "sender@example.test", "owner@example.test")


def test_email_disabled_until_all_required_settings_exist(monkeypatch, capsys):
    assert event_day_guard.email_settings() is None
    event_day_guard.EmailAlertMonitor("https://example.test").observe(False)
    assert "not configured" in capsys.readouterr().out
    monkeypatch.setenv("EVALDAY_SMTP_HOST", "smtp.example.test")
    assert event_day_guard.email_settings() is None
    for name, value in {"EVALDAY_SMTP_USERNAME": "user", "EVALDAY_SMTP_PASSWORD": "super-secret",
                        "EVALDAY_ALERT_FROM": "sender@example.test", "EVALDAY_ALERT_TO": "owner@example.test",
                        "EVALDAY_SMTP_PORT": ""}.items():
        monkeypatch.setenv(name, value)
    assert event_day_guard.email_settings().port == 587
    assert "super-secret" not in repr(event_day_guard.email_settings())


@pytest.mark.parametrize("port", [587, 465])
def test_email_always_uses_verified_tls_before_authentication(monkeypatch, port):
    calls = []

    class Client:
        def __init__(self, host, chosen_port, **kwargs):
            assert host == "smtp.example.test" and chosen_port == port
            assert kwargs["timeout"] == 10
            if port == 465:
                assert kwargs["context"].verify_mode == ssl.CERT_REQUIRED
            calls.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, *, context):
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            calls.append("tls")

        def login(self, username, password):
            assert (username, password) == ("user", "super-secret")
            assert port == 465 or "tls" in calls
            calls.append("login")

        def send_message(self, message):
            assert "OUTAGE" in message["Subject"]
            assert "super-secret" not in message.as_string()
            calls.append("send")

    monkeypatch.setattr(event_day_guard.smtplib, "SMTP", Client)
    monkeypatch.setattr(event_day_guard.smtplib, "SMTP_SSL", Client)
    assert event_day_guard.send_email_alert(configuration(port), "https://example.test", "outage") is True
    assert calls[-2:] == ["login", "send"]


def test_email_failure_is_secret_safe_and_does_not_raise(monkeypatch, capsys):
    def unavailable(*_, **__):
        raise smtplib.SMTPAuthenticationError(535, b"super-secret rejected")

    monkeypatch.setattr(event_day_guard.smtplib, "SMTP", unavailable)
    assert event_day_guard.send_email_alert(configuration(), "https://example.test", "outage") is False
    output = capsys.readouterr().out
    assert "super-secret" not in output
    assert "monitoring continues" in output


def test_email_only_on_outage_and_recovery_and_state_survives_next_run(monkeypatch, tmp_path):
    delivered = []
    monkeypatch.setattr(event_day_guard, "email_settings", configuration)
    monkeypatch.setattr(event_day_guard, "send_email_alert", lambda _config, _url, state: delivered.append(state) or True)
    path = str(tmp_path / "health.json")
    monitor = event_day_guard.EmailAlertMonitor("https://example.test", state_file=path)
    for healthy in (True, False, False, False):
        monitor.observe(healthy)
    restarted = event_day_guard.EmailAlertMonitor("https://example.test", state_file=path)
    restarted.observe(False)
    restarted.observe(True)
    restarted.observe(True)
    assert delivered == ["outage", "healthy"]
    assert "super-secret" not in Path(path).read_text()


def test_failed_delivery_retries_at_most_once_per_five_minutes(monkeypatch):
    now = [1000.0]
    delivered = []
    monkeypatch.setattr(event_day_guard.time, "time", lambda: now[0])
    monkeypatch.setattr(event_day_guard, "email_settings", configuration)
    monkeypatch.setattr(event_day_guard, "send_email_alert", lambda _config, _url, state: delivered.append(state) and False)
    monitor = event_day_guard.EmailAlertMonitor("https://example.test")
    for _ in range(5):
        monitor.observe(False)
        now[0] += 60
    assert delivered == ["outage"]
    monitor.observe(False)
    assert delivered == ["outage", "outage"]


def test_scheduled_probe_budget_fits_five_minute_job():
    workflow = (Path(__file__).parents[1] / ".github/workflows/event-day-watchdog.yml").read_text(encoding="utf-8")
    baseline = workflow.split("  scheduled_keepalive:", 1)[1].split("  primary_1:", 1)[0]
    values = {name: int(re.search(rf"--{name} (\d+)", baseline).group(1)) for name in ("retry-count", "retry-delay-seconds", "timeout-seconds")}
    maximum_probe_seconds = 2 * (values["retry-count"] * values["timeout-seconds"] + (values["retry-count"] - 1) * values["retry-delay-seconds"])
    assert maximum_probe_seconds == 140
    assert "actions/cache/save@v4" in baseline and "if: always()" in baseline


def test_health_url_never_accepts_credentials_for_logs_or_alerts():
    with pytest.raises(ValueError):
        event_day_guard.health_urls("https://username:secret@example.test")
