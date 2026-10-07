from __future__ import annotations

import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app import report_exports


def test_export_releases_a_single_database_connection_before_rendering(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'single-connection.db'}",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.1,
        connect_args={"check_same_thread": False},
    )
    session = Session(engine)
    rendered = threading.Event()

    def prepare(db):
        assert db.execute(text("select 1")).scalar() == 1
        assert engine.pool.checkedout() == 1
        return object()

    def render(_snapshot):
        with engine.connect() as connection:
            assert connection.execute(text("select 1")).scalar() == 1
        rendered.set()
        return object()

    def save(_workbook, output):
        output.write(b"xlsx")

    monkeypatch.setattr(report_exports, "prepare_management_report_export", prepare)
    monkeypatch.setattr(report_exports, "build_management_report_workbook_from_export", render)
    monkeypatch.setattr(report_exports, "save_management_report", save)

    try:
        path = report_exports.create_management_report_file(session, directory=tmp_path)
        assert rendered.is_set()
        assert path.read_bytes() == b"xlsx"
        assert engine.pool.checkedout() == 0
    finally:
        session.close()
        engine.dispose()
        if "path" in locals():
            path.unlink(missing_ok=True)


def test_export_temp_file_is_removed_when_rendering_fails(monkeypatch, tmp_path):
    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    session = FakeSession()
    monkeypatch.setattr(report_exports, "prepare_management_report_export", lambda _db: object())
    monkeypatch.setattr(
        report_exports,
        "build_management_report_workbook_from_export",
        lambda _snapshot: object(),
    )

    def fail(_workbook, _output):
        raise RuntimeError("render failed")

    monkeypatch.setattr(report_exports, "save_management_report", fail)

    try:
        report_exports.create_management_report_file(session, directory=tmp_path)
    except RuntimeError as exc:
        assert str(exc) == "render failed"
    else:
        raise AssertionError("Expected report rendering to fail")

    assert session.closed is True
    assert list(tmp_path.glob("*.xlsx")) == []
