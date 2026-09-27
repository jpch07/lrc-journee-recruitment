"""Isolated tests for the temporary instrumentation; no external services."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from time import sleep

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from starlette.concurrency import run_in_threadpool

from app import request_timing as timing


NOW = datetime(2026, 9, 26, 16, tzinfo=timezone.utc)
FUTURE = datetime(2026, 9, 28, tzinfo=timezone.utc)


def records(stderr_text):
    """Parse the actual bounded stderr output, not the global logging setup."""
    prefix = 'LRC_TIMING_V1 '
    return [json.loads(line[len(prefix):]) for line in stderr_text.splitlines()
            if line.startswith(prefix)]


@pytest.fixture
def diagnostic_engine(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'diagnostic-unit.db'),
                           connect_args={'check_same_thread': False})
    timing.install_engine_timing(engine)
    yield engine
    engine.dispose()


def make_app(engine, *, expires=FUTURE, clock=lambda: NOW):
    application = FastAPI()

    @timing.timed('workspace')
    def workspace():
        with engine.connect() as connection:
            connection.exec_driver_sql('SELECT 1').scalar_one()
        return 'workspace-placeholder'

    @timing.timed('runtime')
    def runtime():
        with engine.connect() as connection:
            connection.exec_driver_sql('SELECT 2').scalar_one()

    @application.middleware('http')
    async def existing_middleware(request, call_next):
        if request.url.path.startswith('/api/'):
            await run_in_threadpool(workspace)
            await run_in_threadpool(runtime)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @application.get('/api/admin/journeys/{journey_id}/dashboard')
    def dashboard(journey_id: str, n: int = 1):
        assert 1 <= n <= 4
        with engine.connect() as connection:
            for _ in range(n):
                connection.execute(text('SELECT :value'), {'value': 'PRIVATE_SQL_VALUE'}).scalar_one()
        return JSONResponse({'status': 'unchanged'}, headers={'Server-Timing': 'prior;dur=1.0'})

    @application.get('/health/live')
    def live():
        return {'status': 'ok'}

    @application.get('/health/ready')
    @timing.timed('ready')
    def ready():
        with engine.connect() as connection:
            with timing.phase('acquire'):
                sleep(0.001)
            with timing.phase('ping'):
                connection.exec_driver_sql('SELECT 1').scalar_one()
            with timing.phase('revision'):
                connection.exec_driver_sql('SELECT 2').scalar_one()
        return {'status': 'ready'}

    @application.get('/api/view/error')
    def expected_error():
        raise HTTPException(409, 'Existing failure')

    @application.get('/api/view/sql-error')
    def sql_error():
        with engine.connect() as connection:
            try:
                connection.exec_driver_sql('SELECT * FROM PRIVATE_MISSING_TABLE')
            except DBAPIError:
                raise HTTPException(409, 'Handled failure')

    @application.post('/api/view/echo')
    async def echo(request: Request):
        # The application may use data; the instrumentation must never log it.
        content = await request.body()
        return JSONResponse({'length': len(content)})

    @application.get('/api/view/stream')
    def stream():
        async def chunks():
            yield b'part-1\n'
            await asyncio.sleep(0)
            yield b'part-2\n'
        return StreamingResponse(chunks(), media_type='text/plain')

    @application.get('/static/example.js')
    def static():
        return {'static': True}

    application.add_middleware(timing.RequestTimingMiddleware, expires_at=expires, clock=clock)
    return application


def test_timing_records_sql_across_basehttp_and_workers(diagnostic_engine, capsys):
    application = make_app(diagnostic_engine)
    with TestClient(application) as client:
        response = client.get('/api/admin/journeys/PRIVATE_ID/dashboard?secret=PRIVATE_QUERY',
                              headers={'Cookie': 'secret=PRIVATE_COOKIE'})
    assert response.status_code == 200
    assert response.json() == {'status': 'unchanged'}
    assert response.headers['cache-control'] == 'no-store'
    header = response.headers['server-timing']
    assert 'prior;dur=1.0' in header
    assert 'lrc_queries;desc="3"' in header
    captured = capsys.readouterr().err
    record, = records(captured)
    assert record['sql_count'] == 3
    assert record['sql_errors'] == 0
    assert record['category'] == 'admin_dashboard'
    assert set(record['phases_ms']) == {'workspace', 'runtime'}
    assert set(record['sql_by_phase_ms']) == {'workspace', 'runtime', 'handler'}
    assert len(response.headers['x-lrc-timing-id']) == 12
    assert all(secret not in captured + header for secret in [
        'PRIVATE_ID', 'PRIVATE_QUERY', 'PRIVATE_COOKIE', 'PRIVATE_SQL_VALUE', 'SELECT'])


def test_readiness_breakdown_and_liveness(diagnostic_engine, capsys):
    with TestClient(make_app(diagnostic_engine)) as client:
        live = client.get('/health/live')
        ready = client.get('/health/ready')
    assert live.json() == {'status': 'ok'}
    assert 'lrc_queries;desc="0"' in live.headers['server-timing']
    assert ready.json() == {'status': 'ready'}
    record = records(capsys.readouterr().err)[1]
    assert record['sql_count'] == 2
    assert set(record['phases_ms']) == {'ready', 'acquire', 'ping', 'revision'}
    assert set(record['sql_by_phase_ms']) == {'ping', 'revision'}


def test_expired_capture_leaves_headers_unchanged(diagnostic_engine, capsys):
    with TestClient(make_app(diagnostic_engine, expires=NOW)) as client:
        response = client.get('/api/admin/journeys/x/dashboard')
    assert response.headers['server-timing'] == 'prior;dur=1.0'
    assert 'x-lrc-timing-id' not in response.headers
    assert records(capsys.readouterr().err) == []


def test_static_requests_are_not_instrumented(diagnostic_engine, capsys):
    with TestClient(make_app(diagnostic_engine)) as client:
        response = client.get('/static/example.js')
    assert response.status_code == 200
    assert 'server-timing' not in response.headers
    assert records(capsys.readouterr().err) == []


def test_expected_error_is_unchanged(diagnostic_engine, capsys):
    with TestClient(make_app(diagnostic_engine)) as client:
        response = client.get('/api/view/error')
    assert response.status_code == 409
    assert response.json() == {'detail': 'Existing failure'}
    assert records(capsys.readouterr().err)[0]['status'] == 409


def test_failed_sql_is_counted_without_logging_its_details(diagnostic_engine, capsys):
    with TestClient(make_app(diagnostic_engine)) as client:
        response = client.get('/api/view/sql-error')
    assert response.status_code == 409
    captured = capsys.readouterr().err
    record, = records(captured)
    assert record['sql_errors'] == 1
    assert record['sql_count'] == 3
    assert 'PRIVATE_MISSING_TABLE' not in captured


def test_instrumentation_does_not_read_or_log_body(diagnostic_engine, capsys):
    payload = b'PRIVATE_BODY_DO_NOT_LOG'
    with TestClient(make_app(diagnostic_engine)) as client:
        response = client.post('/api/view/echo', content=payload)
    assert response.status_code == 200
    assert response.json() == {'length': len(payload)}
    captured = capsys.readouterr().err
    assert len(records(captured)) == 1
    assert 'PRIVATE_BODY_DO_NOT_LOG' not in captured


def test_streaming_body_preserved(diagnostic_engine):
    with TestClient(make_app(diagnostic_engine)) as client:
        response = client.get('/api/view/stream')
    assert response.status_code == 200
    assert response.content == b'part-1\npart-2\n'


def test_sql_listener_installation_is_idempotent(diagnostic_engine, capsys):
    timing.install_engine_timing(diagnostic_engine)
    timing.install_engine_timing(diagnostic_engine)
    with TestClient(make_app(diagnostic_engine)) as client:
        client.get('/api/admin/journeys/x/dashboard')
    assert records(capsys.readouterr().err)[0]['sql_count'] == 3


def test_sequential_requests_have_separate_counters(diagnostic_engine, capsys):
    with TestClient(make_app(diagnostic_engine)) as client:
        client.get('/api/admin/journeys/x/dashboard?n=3')
        client.get('/health/live')
    first, second = records(capsys.readouterr().err)
    assert first['sql_count'] == 5
    assert second['sql_count'] == 0
    assert first['id'] != second['id']
    assert timing._CURRENT.get() is None


def test_concurrent_requests_do_not_share_counters(diagnostic_engine, capsys):
    async def run_requests():
        transport = httpx.ASGITransport(app=make_app(diagnostic_engine))
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            return await asyncio.gather(
                client.get('/api/admin/journeys/x/dashboard?n=1'),
                client.get('/api/admin/journeys/y/dashboard?n=4'),
            )
    responses = asyncio.run(run_requests())
    assert all(response.status_code == 200 for response in responses)
    captured_records = records(capsys.readouterr().err)
    assert sorted(record['sql_count'] for record in captured_records) == [3, 6]
    assert len({record['id'] for record in captured_records}) == 2


def test_logging_failure_does_not_fail_request(diagnostic_engine, monkeypatch):
    def broken_log(self, record):
        raise OSError('simulated logging failure')
    monkeypatch.setattr(timing.RequestTimingMiddleware, '_log', broken_log)
    with TestClient(make_app(diagnostic_engine)) as client:
        response = client.get('/health/live')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_phase_without_request_keeps_exception():
    @timing.timed('workspace')
    def raises():
        raise ValueError('original error')
    with pytest.raises(ValueError, match='original error'):
        raises()
    assert timing._CURRENT.get() is None


def test_log_budgets_do_not_remove_timing_headers(capsys):
    async def inner(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})
    middleware = timing.RequestTimingMiddleware(inner, clock=lambda: NOW)
    middleware._budgets = {'health': 1, 'api': 1}
    async def exercise():
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        messages = []
        async def send(message):
            messages.append(message)
        for _ in range(2):
            await middleware({'type':'http', 'path':'/health/live'}, receive, send)
        return messages
    messages = asyncio.run(exercise())
    assert len(records(capsys.readouterr().err)) == 1
    starts = [message for message in messages if message['type']=='http.response.start']
    assert len(starts) == 2
    assert all(any(k==b'server-timing' for k,v in message['headers']) for message in starts)


def test_numeric_logging_survives_migration_logging_configuration():
    """Reproduce the real config interaction in a child process, not pytest's logger.

    The diagnostic is imported BEFORE fileConfig, just like app startup. The
    old uvicorn.error implementation would emit no record in this scenario.
    No database is opened and no migration or external connection is executed.
    """
    root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent("""
        from contextlib import asynccontextmanager
        from datetime import datetime, timezone
        import json
        import logging
        from logging.config import fileConfig
        from pathlib import Path
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import request_timing as timing

        old_sink = logging.getLogger('uvicorn.error')
        old_sink.disabled = False
        old_sink.setLevel(logging.INFO)
        @asynccontextmanager
        async def lifespan(app):
            fileConfig(str(Path('alembic.ini').resolve()))
            assert old_sink.disabled is True
            yield
        app = FastAPI(lifespan=lifespan)
        @app.get('/health/live')
        def live():
            return {'status': 'ok'}
        app.add_middleware(
            timing.RequestTimingMiddleware,
            expires_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
            clock=lambda: datetime(2026, 9, 26, 16, tzinfo=timezone.utc),
        )
        with TestClient(app) as client:
            response = client.get('/health/live')
            assert response.status_code == 200
            assert response.json() == {'status': 'ok'}
            assert 'lrc_queries;desc="0"' in response.headers['server-timing']
        assert old_sink.disabled is True  # The fix must not re-enable other loggers.
        print('MIGRATION_LOG_CONFIG_REPRO_OK')
    """)
    completed = subprocess.run(
        [sys.executable, '-c', script], cwd=root,
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert 'MIGRATION_LOG_CONFIG_REPRO_OK' in completed.stdout
    emitted, = records(completed.stderr)
    assert emitted['category'] == 'health_live'
    assert emitted['status'] == 200
    assert emitted['sql_count'] == 0


def test_stderr_write_failure_keeps_response_and_timing_headers(monkeypatch):
    """Exercise the actual output sink failure without hiding app failures."""
    class BrokenStream:
        def write(self, value):
            raise OSError('simulated stderr failure')
        def flush(self):
            raise OSError('simulated stderr failure')
    async def inner(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})
    messages = []
    async def exercise():
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            messages.append(message)
        middleware = timing.RequestTimingMiddleware(inner, clock=lambda: NOW)
        await middleware({'type': 'http', 'path': '/health/live'}, receive, send)
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stderr', BrokenStream())
        asyncio.run(exercise())
    assert messages[0]['status'] == 200
    assert any(key == b'server-timing' for key, value in messages[0]['headers'])
    assert messages[1]['body'] == b'ok'
