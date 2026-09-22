from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from scripts import check_storage_usage as storage


@pytest.fixture(autouse=True)
def clean_database():
    # All measurements use fake result objects; no shared test or live DB access.
    yield


class Result:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value

    def scalar_one(self):
        return self.value

    def __iter__(self):
        return iter(self.value)


def fake_sessions(values, queries):
    class Session:
        def execute(self, statement):
            queries.append(str(statement))
            value = values.pop(0)
            if isinstance(value, Exception):
                raise value
            return Result(value)

    @contextmanager
    def factory():
        yield Session()
    return factory


def test_aiven_default_and_explicit_provider_budgets():
    assert storage.configured_allowance("postgresql://user@sample.aivencloud.com/database")[0] == 1_000_000_000
    assert storage.configured_allowance("postgresql://user@other.example/database", 750_000_000)[0] == 750_000_000
    assert storage.configured_allowance("cockroachdb+psycopg://user@cluster.cockroachlabs.cloud/database")[0] == 10 * 1024**3
    with pytest.raises(storage.StorageCheckError, match="Set --database-allowance"):
        storage.configured_allowance("postgresql://user@other.example/database")
    with pytest.raises(storage.StorageCheckError, match="positive"):
        storage.configured_allowance("postgresql://user@sample.aivencloud.com/database", 0)


def test_postgres_measures_physical_current_database_not_row_sum():
    queries = []
    measured = storage.database_measurement("postgresql://user@sample.aivencloud.com/database",
        fake_sessions([("database", 123456789)], queries))
    assert measured["bytes"] == 123456789
    assert measured["database"] == "database"
    assert "pg_database_size(current_database())" in queries[0]
    assert len(queries) == 1
    assert "indexes and TOAST" in measured["measurement"]
    assert "not total cluster quota" in measured["scope"]
    assert "other databases" in measured["scope"]


def test_cockroach_is_explicitly_approximate_and_quotes_identifiers():
    queries = []
    result = storage.database_measurement("cockroachdb+psycopg://user@cluster.example/database",
        fake_sessions([[('ordinary_table',), ('table"with_quote',)], 25, 75], queries))
    assert result["bytes"] == 100
    assert "approximate" in result["measurement"]
    assert "replication" in result["scope"]
    assert '"table""with_quote"' in queries[2]


def test_logical_query_failure_does_not_return_partial_or_zero_usage():
    queries = []
    with pytest.raises(RuntimeError):
        storage.database_logical_bytes(fake_sessions([[('first',), ('second',)], 20, RuntimeError("query failed")], queries))
    with pytest.raises(storage.StorageCheckError, match="No application tables"):
        storage.database_logical_bytes(fake_sessions([[]], []))


@pytest.mark.parametrize("value", [None, -1])
def test_missing_or_negative_physical_measurement_is_unknown(value):
    with pytest.raises(storage.StorageCheckError, match="usage is unknown"):
        storage.database_measurement("postgresql://user@sample.aivencloud.com/database",
                                     fake_sessions([("database", value)], []))


def test_disabled_r2_is_not_reported_as_zero(monkeypatch, capsys):
    monkeypatch.setattr(storage, "settings", SimpleNamespace(database_url="postgresql://user@sample.aivencloud.com/database"))
    monkeypatch.setattr(storage, "database_measurement", lambda _url: {
        "providerKind": "postgresql", "bytes": 500_000_000, "measurement": "physical", "scope": "current database only"})
    monkeypatch.setattr(storage, "enabled", lambda: False)
    assert storage.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["database"]["percent"] == 50
    assert report["database"]["level"] == "NOTICE"
    assert report["r2"]["status"] == "not configured"
    assert "bytes" not in report["r2"]


def test_cli_failure_does_not_leak_secret_or_claim_ok(monkeypatch, capsys):
    monkeypatch.setattr(storage, "settings", SimpleNamespace(database_url="postgresql://user@sample.aivencloud.com/database"))
    def failed(_url):
        raise RuntimeError("postgresql://user:do-not-print@host/database")
    monkeypatch.setattr(storage, "database_measurement", failed)
    assert storage.main(["--json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "do-not-print" not in captured.err
    report = json.loads(captured.err)
    assert report["status"] == "error"
    assert "unknown" in report["message"]


def test_cli_fails_at_configured_budget_threshold(monkeypatch, capsys):
    monkeypatch.setattr(storage, "settings", SimpleNamespace(database_url="postgresql://user@other.example/database"))
    monkeypatch.setattr(storage, "database_measurement", lambda _url: {
        "providerKind": "postgresql", "bytes": 900, "measurement": "physical", "scope": "current database only"})
    monkeypatch.setattr(storage, "r2_bytes", lambda: (3, 20))
    assert storage.main(["--database-allowance-bytes", "1000", "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["database"]["level"] == "CRITICAL"
    assert report["status"] == "threshold reached"
    assert report["r2"]["objects"] == 3


def test_r2_listing_failure_is_unknown_not_zero(monkeypatch, capsys):
    monkeypatch.setattr(storage, "settings", SimpleNamespace(database_url="postgresql://user@sample.aivencloud.com/database"))
    monkeypatch.setattr(storage, "database_measurement", lambda _url: {
        "providerKind": "postgresql", "bytes": 1, "measurement": "physical", "scope": "current database only"})
    def failed():
        raise RuntimeError("credential-containing provider exception")
    monkeypatch.setattr(storage, "r2_bytes", failed)
    assert storage.main(["--json"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "credential-containing" not in captured.err
