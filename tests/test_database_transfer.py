from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import UUID
import zipfile

import pytest
from sqlalchemy import inspect, select, text

from scripts import database_transfer as transfer


@pytest.fixture(autouse=True)
def clean_database():
    # This module creates only per-test tmp_path databases. Avoid the shared
    # application fixture so parallel migration/load-tool checks cannot collide.
    yield


def db(tmp_path, name, *, populated=False):
    engine = transfer.engine_for(f"sqlite:///{(tmp_path / name).as_posix()}")
    with engine.begin() as connection:
        transfer.metadata().create_all(connection)
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES (:revision)"),
                           {"revision": transfer.current_revision()})
        if populated:
            tables = transfer.metadata().tables
            connection.execute(tables["platform_accounts"].insert(), {
                "id": "owner", "username": "sample owner", "password_hash": "hash-preserved"})
            connection.execute(tables["assessment_systems"].insert(), {
                "id": "system", "owner_platform_account_id": "owner", "name": "Test workspace",
                "slug": "test-workspace", "draft_json": '{"unchanged":true}'})
            connection.execute(tables["evaluator_directory"].insert(), {
                "id": "directory", "system_id": "system", "name": "Nickname",
                "full_name": "Sample Assessor", "phone_number": "+96100000000"})
            connection.execute(tables["user_accounts"].insert(), {
                "id": "account", "system_id": "system", "platform_account_id": "owner",
                "directory_id": "directory", "username": "Nickname", "password_hash": "existing-hash",
                "managed_password": "synthetic-test-only", "is_owner": True, "can_admin": True})
            connection.execute(tables["journeys"].insert(), {
                "id": "journey", "system_id": "system", "name": "Completed event", "status": "completed",
                "event_date": date(2026, 8, 22), "public_token": "synthetic-public-token"})
            connection.execute(tables["evaluators"].insert(), {
                "id": "assessor", "journey_id": "journey", "name": "Nickname", "role": "overall"})
            connection.execute(tables["recruits"].insert(), {
                "id": "participant", "journey_id": "journey", "name": "Sample participant",
                "date_of_birth": date(2000, 2, 29), "photo_data": b"\x00\xffphoto\x01",
                "photo_object_key": "private/photo-key", "photo_sha256": "0" * 64,
                "photo_size": 8, "attendance_comment": "Preserve exactly\nArabic: مرحبا"})
            connection.execute(tables["journey_permissions"].insert(), {
                "id": "permission", "journey_id": "journey", "account_id": "account", "can_attendance": True})
            # Child sorts before parent lexically: restoration must reorder it.
            connection.execute(tables["assignment_rounds"].insert(), {
                "id": "z-parent", "journey_id": "journey", "activity_code": "escape_room",
                "version": 1, "seed": "seed", "created_by": "owner"})
            connection.execute(tables["assignment_rounds"].insert(), {
                "id": "a-child", "journey_id": "journey", "activity_code": "negotiation",
                "version": 1, "seed": "seed", "created_by": "owner", "reused_from_id": "z-parent"})
            connection.execute(tables["assignments"].insert(), {
                "id": "task", "round_id": "a-child", "evaluator_id": "assessor", "recruit_id": "participant"})
            connection.execute(tables["evaluation_submissions"].insert(), {
                "id": "submission", "assignment_id": "task", "journey_id": "journey", "activity_code": "negotiation",
                "evaluator_id": "assessor", "recruit_id": "participant", "score": Decimal("4.12345"),
                "responses_json": '{"criterion":4.25}', "raw_payload_json": '{"verbatim":"keep"}',
                "comments": "Original comment", "status": "submitted"})
            connection.execute(tables["general_assessments"].insert(), {
                "recruit_id": "participant", "punctuality": Decimal("4.50"), "respect": Decimal("0"),
                "seriousness": None, "values_json": '{"factor":"4.50"}', "notes": "Private note"})
            connection.execute(tables["platform_sessions"].insert(), {
                "token_hash": "a" * 64, "account_id": "owner", "csrf_token": "synthetic-csrf",
                "expires_at": datetime(2026, 9, 30, tzinfo=timezone.utc)})
            connection.execute(tables["idempotency_records"].insert(), {
                "id": "idempotency", "scope": "submission", "request_key": "request-key",
                "response_json": '{"status":"submitted"}'})
    return engine


def test_typed_value_codec():
    values = [None, True, 42, "a\nمرحبا", b"\x00\xff", Decimal("4.25000"), date(2000, 2, 29),
              datetime(2026, 9, 22, 12, 34, 56, 789, timezone.utc), UUID(int=1)]
    for value in values:
        assert transfer.decode(transfer.encode(value)) == value


def test_sqlite_naive_and_postgres_aware_timestamp_rows_have_identical_hashes():
    # SQLite's DateTime processor returns naive UTC; PostgreSQL timestamptz
    # returns aware instants (possibly in the connection's timezone).
    naive = datetime(2026, 9, 22, 12, 34, 56, 123456)
    utc = naive.replace(tzinfo=timezone.utc)
    beirut = utc.astimezone(timezone(timedelta(hours=3)))
    sqlite_row = {"id": "test-idempotency-record", "created_at": naive, "response_json": "{}"}
    postgres_row = {**sqlite_row, "created_at": utc}
    offset_row = {**sqlite_row, "created_at": beirut}
    assert transfer.row_bytes(sqlite_row) == transfer.row_bytes(postgres_row)
    assert transfer.row_bytes(sqlite_row) == transfer.row_bytes(offset_row)
    assert transfer.decode(transfer.encode(naive)) == utc


def test_export_makes_sqlite_timestamp_utc_explicit_for_postgres_restore(tmp_path):
    source = db(tmp_path, "sqlite-source.db", populated=True)
    backup = tmp_path / "utc-transfer.zip"
    transfer.export_database(source, backup)
    with zipfile.ZipFile(backup) as archive:
        row = json.loads(archive.read("tables/idempotency_records.jsonl"))
    timestamp = transfer.decode(row["created_at"])
    assert timestamp.tzinfo == timezone.utc
    with source.connect() as connection:
        table = transfer.metadata().tables["idempotency_records"]
        sqlite_timestamp = connection.execute(select(table.c.created_at)).scalar_one()
    assert sqlite_timestamp.tzinfo is None
    assert timestamp == sqlite_timestamp.replace(tzinfo=timezone.utc)
    source.dispose()


def test_full_restore_preserves_all_values_and_sessions(tmp_path):
    source = db(tmp_path, "source.db", populated=True)
    target = db(tmp_path, "target.db")
    archive = tmp_path / "full.zip"
    before = transfer.export_database(source, archive)
    assert before["tables"]["platform_sessions"]["rows"] == 1
    assert before["tables"]["idempotency_records"]["rows"] == 1
    assert before["tables"]["user_accounts"]["rows"] == 1
    result = transfer.restore_database(target, archive, confirm=True)
    assert result["dataSha256"] == before["dataSha256"]
    with target.connect() as connection:
        transfer.verify_connection(connection, before)
        table = transfer.metadata().tables["recruits"]
        assert connection.execute(select(table.c.photo_data)).scalar_one() == b"\x00\xffphoto\x01"
    source.dispose()
    target.dispose()


def test_restore_dry_run_does_not_create_schema(tmp_path):
    source = db(tmp_path, "source.db", populated=True)
    target = transfer.engine_for(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    archive = tmp_path / "full.zip"
    transfer.export_database(source, archive)
    assert transfer.restore_database(target, archive, initialize_empty=True)["dryRun"]
    assert inspect(target).get_table_names() == []
    transfer.restore_database(target, archive, confirm=True, initialize_empty=True)
    assert set(transfer.metadata().tables).issubset(inspect(target).get_table_names())
    source.dispose()
    target.dispose()


def test_refuses_nonempty_account_table_even_without_journeys(tmp_path):
    source = db(tmp_path, "source.db", populated=True)
    target = db(tmp_path, "target.db")
    archive = tmp_path / "full.zip"
    transfer.export_database(source, archive)
    with target.begin() as connection:
        connection.execute(transfer.metadata().tables["platform_accounts"].insert(), {
            "id": "protected-owner", "username": "existing", "password_hash": "existing"})
    with pytest.raises(transfer.TransferError, match="not empty"):
        transfer.restore_database(target, archive, confirm=True)
    source.dispose()
    target.dispose()


def test_refuses_unknown_tables_and_missing_columns(tmp_path):
    source = db(tmp_path, "source.db")
    with source.begin() as connection:
        connection.execute(text("CREATE TABLE forgotten_records (id TEXT)"))
    with pytest.raises(transfer.TransferError, match="Unrecognized tables"):
        transfer.export_database(source, tmp_path / "bad.zip")
    with zipfile.ZipFile(tmp_path / "bad.zip") as archive:
        assert "manifest.json" not in archive.namelist()
    with pytest.raises(FileExistsError):
        transfer.export_database(source, tmp_path / "bad.zip")
    source.dispose()


def rewrite_archive(source: Path, destination: Path, *, valid_manifest=False):
    with zipfile.ZipFile(source) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    name = "tables/evaluation_submissions.jsonl"
    row = json.loads(contents[name])
    row["assignment_id"] = "nonexistent-task"
    contents[name] = transfer.json_bytes(row) + b"\n"
    if valid_manifest:
        manifest = json.loads(contents["manifest.json"])
        manifest["tables"]["evaluation_submissions"]["sha256"] = hashlib.sha256(contents[name]).hexdigest()
        manifest["dataSha256"] = hashlib.sha256(transfer.json_bytes(manifest["tables"])).hexdigest()
        contents["manifest.json"] = transfer.json_bytes(manifest)
    with zipfile.ZipFile(destination, "w") as archive:
        for name, value in contents.items():
            archive.writestr(name, value)


def test_checksum_detects_corruption_before_target_write(tmp_path):
    source = db(tmp_path, "source.db", populated=True)
    target = db(tmp_path, "target.db")
    archive, corrupt = tmp_path / "full.zip", tmp_path / "corrupt.zip"
    transfer.export_database(source, archive)
    rewrite_archive(archive, corrupt)
    with pytest.raises(transfer.TransferError, match="checksum/count mismatch"):
        transfer.restore_database(target, corrupt, confirm=True)
    with target.connect() as connection:
        transfer.assert_empty(connection)
    source.dispose()
    target.dispose()


def test_foreign_key_failure_rolls_back_all_inserted_rows(tmp_path):
    from sqlalchemy.exc import IntegrityError
    source = db(tmp_path, "source.db", populated=True)
    target = db(tmp_path, "target.db")
    archive, invalid = tmp_path / "full.zip", tmp_path / "invalid.zip"
    transfer.export_database(source, archive)
    rewrite_archive(archive, invalid, valid_manifest=True)
    with pytest.raises(IntegrityError):
        transfer.restore_database(target, invalid, confirm=True)
    with target.connect() as connection:
        transfer.assert_empty(connection)
    source.dispose()
    target.dispose()


def test_detects_parity_change_and_self_reference_cycle(tmp_path):
    source = db(tmp_path, "source.db", populated=True)
    manifest = transfer.export_database(source, tmp_path / "full.zip")
    with source.begin() as connection:
        connection.execute(text("UPDATE user_accounts SET managed_password='synthetic-other'"))
    with source.connect() as connection, pytest.raises(transfer.TransferError, match="Full-row parity"):
        transfer.verify_connection(connection, manifest)
    table = transfer.metadata().tables["assignment_rounds"]
    with pytest.raises(transfer.TransferError, match="Circular"):
        transfer.ordered_self_references(table, [{"id": "a", "reused_from_id": "b"},
                                                 {"id": "b", "reused_from_id": "a"}])
    source.dispose()


def test_workspace_seed_preserves_only_configuration_and_accounts(tmp_path):
    source = db(tmp_path, "source.db", populated=True)
    target = db(tmp_path, "target.db")
    with source.begin() as connection:
        tables = transfer.metadata().tables
        connection.execute(tables["assessment_system_versions"].insert(), {
            "id": "config-version", "system_id": "system", "version": 1,
            "definition_json": '{"accessProfiles":["unchanged"]}', "published_by": "owner"})
        connection.execute(tables["platform_accounts"].insert(), {
            "id": "other-owner", "username": "Other account", "password_hash": "other"})
        connection.execute(tables["assessment_systems"].insert(), {
            "id": "other-system", "owner_platform_account_id": "other-owner", "name": "Other workspace",
            "slug": "other-workspace", "draft_json": "{}"})
    archive = tmp_path / "seed.zip"
    manifest = transfer.export_database(source, archive, workspace_slug="test-workspace")
    assert manifest["kind"] == "workspace-seed"
    expected_counts = {"platform_accounts": 1, "assessment_systems": 1, "assessment_system_versions": 1,
                       "evaluator_directory": 1, "user_accounts": 1}
    for name, details in manifest["tables"].items():
        assert details["rows"] == expected_counts.get(name, 0)
    with pytest.raises(transfer.TransferError, match="not a full backup"):
        transfer.restore_database(target, archive, confirm=True)
    transfer.restore_database(target, archive, confirm=True, allow_workspace_seed=True)
    with target.connect() as connection:
        transfer.verify_connection(connection, manifest)
        account = connection.execute(select(transfer.metadata().tables["user_accounts"])).mappings().one()
        assert account["managed_password"] == "synthetic-test-only"
        assert account["can_admin"] and account["is_owner"]
    source.dispose()
    target.dispose()
