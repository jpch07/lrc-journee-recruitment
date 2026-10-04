import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AssessmentSystem, UserAccount
from app import sheet_backup_jobs as jobs
from test_viewer_performance_c1 import _login


class FakeReceiver:
    calls = []
    def __init__(self, *args, **kwargs):
        pass
    def call(self, action, payload):
        self.calls.append((action, payload))
        if action == 'status':
            return {'state': 'ready', 'lastComplete': None}
        return {'next': payload.get('sequence', -1) + 1,
                'state': 'complete' if payload.get('operation', {}).get('kind') == 'publish' else 'running'}


@pytest.fixture
def connected(client, monkeypatch):
    session = _login(client)
    with SessionLocal() as db:
        system = db.scalar(select(AssessmentSystem))
        monkeypatch.setenv('LRC_SHEET_BACKUP_WORKSPACE_ID', system.id)
    monkeypatch.setenv('LRC_SHEET_BACKUP_URL', 'https://script.google.com/macros/s/test/exec')
    monkeypatch.setenv('LRC_SHEET_BACKUP_SECRET', 's' * 40)
    monkeypatch.setattr('app.routes_sheet_backup.Receiver', FakeReceiver)
    monkeypatch.setattr(jobs, 'build_export', lambda *a: {})
    monkeypatch.setattr(jobs, 'encode_operations', lambda export: iter([{'kind':'prepare'}, {'kind':'publish'}]))
    FakeReceiver.calls = []
    yield {'X-CSRF-Token': session['csrfToken']}
    jobs.clear_jobs()


def test_manual_job_lifecycle_and_owner_security(client, connected):
    headers = connected
    assert client.get('/api/admin/sheet-backup').json()['connected']
    assert client.post('/api/admin/sheet-backup/start').status_code == 403
    started = client.post('/api/admin/sheet-backup/start', headers=headers)
    assert started.status_code == 200, started.text
    job = started.json()['jobId']
    assert client.post('/api/admin/sheet-backup/start', headers=headers).status_code == 409
    advanced = client.post(f'/api/admin/sheet-backup/{job}/advance', headers=headers)
    assert advanced.json()['progress'] == 1
    done = client.post(f'/api/admin/sheet-backup/{job}/advance', headers=headers)
    assert done.json()['state'] == 'complete'
    assert client.post(f'/api/admin/sheet-backup/{job}/advance', headers=headers).json()['state'] == 'complete'
    assert len([x for x in FakeReceiver.calls if x[0]=='apply']) == 2
    assert 'operation' not in done.text
    with SessionLocal() as db:
        account = db.scalar(select(UserAccount).where(UserAccount.username == 'JP Chaaya'))
        account.is_owner = False
        db.commit()
    assert client.get('/api/admin/sheet-backup').status_code == 403


def test_other_workspace_and_lost_job_fail_closed(client, connected, monkeypatch):
    monkeypatch.setenv('LRC_SHEET_BACKUP_WORKSPACE_ID', 'another-workspace')
    assert not client.get('/api/admin/sheet-backup').json()['configured']
    assert client.post('/api/admin/sheet-backup/start', headers=connected).status_code == 409
    assert not FakeReceiver.calls


def test_unconfigured_has_no_source_export_or_google_request(client, monkeypatch):
    session = _login(client)
    monkeypatch.delenv('LRC_SHEET_BACKUP_URL', raising=False)
    status = client.get('/api/admin/sheet-backup')
    assert status.status_code == 200
    assert not status.json()['connected']
    assert '11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0' in status.text
    assert client.post('/api/admin/sheet-backup/start', headers={'X-CSRF-Token':session['csrfToken']}).status_code == 409


def test_transient_upload_failure_retains_operation_for_retry(client, connected, monkeypatch):
    from app.sheet_backup_export import BackupError
    job = client.post('/api/admin/sheet-backup/start', headers=connected).json()['jobId']
    original = FakeReceiver.call
    def fail(self, action, payload):
        if action == 'apply':
            raise BackupError('Temporary failure')
        return original(self, action, payload)
    monkeypatch.setattr(FakeReceiver, 'call', fail)
    assert client.post(f'/api/admin/sheet-backup/{job}/advance', headers=connected).status_code == 502
    monkeypatch.setattr(FakeReceiver, 'call', original)
    assert client.post(f'/api/admin/sheet-backup/{job}/advance', headers=connected).json()['progress'] == 1
    assert client.post(f'/api/admin/sheet-backup/{job}/cancel', headers=connected).json()['state'] == 'cancelled'
