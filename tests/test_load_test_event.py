"""Safety checks for the opt-in standalone HTTP workload harness."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import pytest

from scripts.load_test_event import (
    AppMemoryMonitor, Metrics, install_safe_server_handler, linux_memory_from_status,
    percentile, process_memory_bytes, record_server_failure,
    run_bounded_workload, safe_exception_classes, server_failure_summary,
    serve_isolated_test_app, validate_test_database,
)


@pytest.fixture(autouse=True)
def clean_database():
    # These are pure guard/metrics tests; override the project's autouse DDL
    # fixture so they never create/drop the shared integration-test database.
    yield


@pytest.mark.parametrize("url", [
    "", "sqlite:///data/journee.db", "sqlite:///:memory:",
    "postgresql://u:p@host/defaultdb", "postgresql://u:p@host/production",
    "postgresql://u:p@cluster.cockroachlabs.cloud/evalday_loadtest_one",
    "postgresql://u:p@host.neon.tech/evalday_loadtest_one",
    "https://evalday.onrender.com/evalday_loadtest_one",
])
def test_load_harness_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):
        validate_test_database(url)


def test_load_harness_sqlite_requires_a_new_explicit_test_file(tmp_path):
    file = tmp_path / "evalday_loadtest_unit.db"
    url = f"sqlite:///{file.as_posix()}"
    assert validate_test_database(url) == url
    file.touch()
    with pytest.raises(ValueError, match="NEW file"):
        validate_test_database(url)


def test_load_harness_allows_dedicated_database_not_same_database():
    test = "postgres://user:secret@host.aivencloud.com:1234/evalday_loadtest_qa?sslmode=require"
    expected = test.replace("postgres://", "postgresql+psycopg://")
    assert validate_test_database(test, "postgres://x:y@host.aivencloud.com:1234/defaultdb") == expected
    with pytest.raises(ValueError, match="production database"):
        validate_test_database(test, test)


def test_load_report_metrics_do_not_include_requests_or_credentials():
    metrics = Metrics()
    metrics.record("login", 0.123, 200)
    metrics.record("login", 0.456, 429)
    summary = metrics.summary()["login"]
    assert summary["requests"] == 2
    assert summary["status"] == {"200": 1, "429": 1}
    assert summary["p95_ms"] == 456
    assert percentile([], 95) == 0


def test_load_workload_deadline_cancels_inflight_work_and_records_failure():
    class StalledLoad:
        metrics = Metrics()
        stopped = False

        async def run(self):
            try:
                await asyncio.sleep(60)
            finally:
                self.stopped = True

    load = StalledLoad()
    asyncio.run(run_bounded_workload(load, 0.01))
    assert load.stopped
    assert load.metrics.errors == [
        "Workload exceeded its bounded runtime; outstanding test requests cancelled"
    ]


def test_server_diagnostics_redact_messages_sql_and_credentials(tmp_path):
    from sqlalchemy.exc import OperationalError

    class DriverError(Exception):
        sqlstate = "53300"

    secret = "postgresql://private-user:private-password@example.invalid/private-db"
    wrapped = OperationalError("SELECT private_column", {"password": secret}, DriverError(secret))
    grouped = ExceptionGroup(secret, [wrapped, ValueError(secret)])
    classes = safe_exception_classes(grouped)
    assert any(item.get("sqlstate") == "53300" for item in classes)
    path = tmp_path / "safe-errors.jsonl"
    record_server_failure(path, grouped)
    record_server_failure(path, grouped)
    report = server_failure_summary(path)
    serialized = path.read_text() + json.dumps(report)
    assert report["events"] == 2
    assert all(item["count"] == 2 for item in report["classes"])
    assert all(value not in serialized for value in (secret, "private-user", "private-password", "SELECT"))


def test_test_server_500_reports_class_and_only_generic_response(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    fixture = FastAPI()
    path = tmp_path / "safe-errors.jsonl"
    install_safe_server_handler(fixture, path)

    @fixture.get("/failing")
    def failing():
        raise PoolTimeout("secret connection URI and SQL must not be output")

    with TestClient(fixture, raise_server_exceptions=False) as client:
        response = client.get("/failing")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert server_failure_summary(path) == {
        "events": 1, "classes": [{"type": "sqlalchemy.exc.TimeoutError", "count": 1}]
    }


def test_private_worker_cannot_be_started_without_harness_sentinel(monkeypatch, tmp_path):
    monkeypatch.delenv("EVALDAY_LOADTEST_WORKER", raising=False)
    with pytest.raises(ValueError, match="Private worker"):
        serve_isolated_test_app(9000, tmp_path / "errors.jsonl")


def test_linux_memory_parser_uses_resident_not_virtual_bytes():
    assert linux_memory_from_status("VmSize:\t900000 kB\nVmRSS:\t2048 kB\nVmHWM:\t4096 kB\n") == {
        "rss_bytes": 2097152, "os_peak_rss_bytes": 4194304, "method": "linux_proc_status"
    }
    assert linux_memory_from_status("VmSize:\t900000 kB\n") is None


def test_memory_monitor_reports_os_high_water_mark_and_sampling_separately():
    readings = iter([
        {"rss_bytes": 1048576, "os_peak_rss_bytes": 2097152, "method": "test_probe"},
        {"rss_bytes": 524288, "os_peak_rss_bytes": 3145728, "method": "test_probe"},
    ])
    monitor = AppMemoryMonitor(123, probe=lambda _pid: next(readings))
    monitor.sample()
    monitor.sample()
    result = monitor.summary()
    assert result["available"] and result["successful_samples"] == 2
    assert result["sampled_peak_rss_bytes"] == 1048576
    assert result["os_peak_rss_bytes"] == 3145728
    assert result["peak_rss_mib"] == 3
    assert result["memory_limit_enforced"] is False


def test_unavailable_memory_is_null_not_a_zero_memory_claim():
    monitor = AppMemoryMonitor(123, probe=lambda _pid: None)
    monitor.sample()
    result = monitor.summary()
    assert not result["available"]
    assert result["peak_rss_bytes"] is None
    assert result["peak_rss_mib"] is None
    assert result["unavailable_reason"]


@pytest.mark.skipif(not sys.platform.startswith(("linux", "win32")), reason="OS memory probe unsupported")
def test_memory_probe_reads_current_test_process_without_dependencies():
    result = process_memory_bytes(os.getpid())
    assert result and result["rss_bytes"] > 0
    assert result["os_peak_rss_bytes"] >= result["rss_bytes"]
