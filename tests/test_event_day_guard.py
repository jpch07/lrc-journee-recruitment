from __future__ import annotations

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
