"""Full-application verification; uses tests/conftest.py's disposable SQLite."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.request_timing import CAPTURE_UNTIL


pytestmark = pytest.mark.skipif(datetime.now(timezone.utc) >= CAPTURE_UNTIL,
                                reason='Temporary production diagnostic has expired')


def test_application_health_timings_without_changing_bodies(client):
    live = client.get('/health/live')
    assert live.status_code == 200
    assert live.json()['status'] == 'ok'
    assert 'lrc_queries;desc="0"' in live.headers['server-timing']
    ready = client.get('/health/ready')
    assert ready.status_code == 200
    assert ready.json() == {'status': 'ready'}
    timing = ready.headers['server-timing']
    for metric in ('lrc_app;', 'lrc_sql;', 'lrc_acquire;', 'lrc_ping;', 'lrc_revision;'):
        assert metric in timing
    assert 'lrc_queries;desc="2"' in timing  # SQLite does not run PostgreSQL SET LOCAL.
    assert ready.headers['cache-control'] == 'no-store'


def test_application_auth_and_workspace_behavior_unchanged(client):
    denied = client.get('/api/admin/journeys')
    assert denied.status_code == 401
    session = client.post('/api/auth/login', json={
        'username': 'JP Chaaya', 'password': 'test-password',
    })
    assert session.status_code == 200
    allowed = client.get('/api/admin/journeys')
    assert allowed.status_code == 200
    assert isinstance(allowed.json(), list)
    timing = allowed.headers['server-timing']
    assert 'lrc_workspace;' in timing
    assert 'lrc_runtime;' in timing
    assert 'test-password' not in timing
    assert 'JP Chaaya' not in timing
    assert allowed.headers['cache-control'] == 'no-store'
