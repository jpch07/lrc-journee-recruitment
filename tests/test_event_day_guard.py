from __future__ import annotations

import io
from pathlib import Path
import re

import pytest

from scripts import event_day_guard


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
