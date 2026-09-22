"""Safety checks for the opt-in standalone HTTP workload harness."""
from __future__ import annotations

import asyncio
import pytest

from scripts.load_test_event import Metrics, percentile, run_bounded_workload, validate_test_database


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
