from app.db import SessionLocal
from app.models import AuditEvent, ManagementCorrection
from test_management_correction_scoring import fixture
from test_viewer_performance_c1 import _login


def setup(client):
    session = _login(client)
    with SessionLocal() as db:
        journey, recruit = fixture(db)
    return f'/api/view/journeys/{journey.id}/recruits/{recruit.id}/corrections', {'X-CSRF-Token': session['csrfToken']}


def operation(client, url, **changes):
    response = client.get(url)
    assert response.status_code == 200, response.text
    state = response.json()
    return dict(revision=state['revision'], configurationSignature=state['configurationSignature'],
                action='set', level='activity', key='sport', value=4, reason='Correction', **changes)


def test_preview_apply_conflict_restore_and_history(client):
    url, headers = setup(client)
    request = operation(client, url)
    preview = client.post(url + '/preview', headers=headers, json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()['after']['activities']['sport']['score'] == 4
    with SessionLocal() as db:
        assert db.query(ManagementCorrection).count() == 0
    request['inputFingerprint'] = preview.json()['inputFingerprint']
    saved = client.put(url, headers=headers, json=request)
    assert saved.status_code == 200, saved.text
    assert client.put(url, headers=headers, json=request).status_code == 409
    state = client.get(url).json()
    assert state['revision'] == 1 and len(state['history']) == 1
    restore = dict(request, action='restore', value=None, revision=1, inputFingerprint=None)
    preview = client.post(url + '/preview', headers=headers, json=restore)
    restore['inputFingerprint'] = preview.json()['inputFingerprint']
    assert client.put(url, headers=headers, json=restore).status_code == 200
    assert len(client.get(url).json()['history']) == 2


def test_csrf_and_changed_inputs_are_blocked(client):
    url, headers = setup(client)
    request = operation(client, url)
    assert client.put(url, json=request).status_code == 403
    request['inputFingerprint'] = 'wrong'
    assert client.put(url, headers=headers, json=request).status_code == 409
    with SessionLocal() as db:
        assert db.query(ManagementCorrection).count() == 0


def test_atomic_audit_failure(client, monkeypatch):
    import app.correction_service as service
    url, headers = setup(client)
    request = operation(client, url)
    preview = client.post(url + '/preview', headers=headers, json=request).json()
    request['inputFingerprint'] = preview['inputFingerprint']
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(service, 'audit', fail)
    import pytest
    with pytest.raises(RuntimeError):
        client.put(url, headers=headers, json=request)
    with SessionLocal() as db:
        assert db.query(ManagementCorrection).count() == 0


def test_undo_color_and_validation(client):
    url, headers = setup(client)
    request = operation(client, url)
    request.update(level='color', key='green', value=None)
    preview = client.post(url + '/preview', headers=headers, json=request).json()
    assert preview['before']['overallScore'] == preview['after']['overallScore']
    request['inputFingerprint'] = preview['inputFingerprint']
    assert client.put(url, headers=headers, json=request).status_code == 200
    state = client.get(url).json()
    request.update(action='undo', eventId=state['history'][0]['id'], revision=state['revision'], inputFingerprint=None)
    preview = client.post(url + '/preview', headers=headers, json=request).json()
    request['inputFingerprint'] = preview['inputFingerprint']
    assert client.put(url, headers=headers, json=request).status_code == 200
    assert client.get(url).json()['color'] is None
    request.update(action='set', revision=2, key='<script>', inputFingerprint=None)
    assert client.post(url + '/preview', headers=headers, json=request).status_code == 422


def test_underlying_grade_change_invalidates_preview(client):
    from app.models import AdminEvaluation
    url, headers = setup(client)
    request = operation(client, url)
    request['inputFingerprint'] = client.post(url + '/preview', headers=headers, json=request).json()['inputFingerprint']
    with SessionLocal() as db:
        original = db.query(AdminEvaluation).filter_by(activity_code='sport').first()
        original.score = 3
        db.commit()
    assert client.put(url, headers=headers, json=request).status_code == 409


def test_wrong_recruit_scope_and_missing_login(client):
    url, headers = setup(client)
    with SessionLocal() as db:
        journey2, recruit2 = fixture(db)
    pieces = url.split('/')
    pieces[-2] = recruit2.id
    assert client.get('/'.join(pieces)).status_code == 404
    client.cookies.clear()
    assert client.get(url).status_code == 401
