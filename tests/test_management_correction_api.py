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


def test_apply_uses_one_snapshot_if_a_grading_input_changes_mid_calculation(client, monkeypatch):
    from app import services
    from app.models import AdminEvaluation
    from app.utils import loads, dumps
    url, headers = setup(client)
    request = operation(client, url)
    approved = client.post(url + '/preview', headers=headers, json=request).json()
    request['inputFingerprint'] = approved['inputFingerprint']
    original_snapshot = services.result_snapshot
    calls = 0
    def interleave(*args, **kwargs):
        nonlocal calls
        result = original_snapshot(*args, **kwargs)
        calls += 1
        if calls == 1:
            with SessionLocal() as writer:
                grade = writer.query(AdminEvaluation).filter(AdminEvaluation.activity_code != 'sport').first()
                grade.responses_json = dumps({key: 5 for key in loads(grade.responses_json, {})})
                grade.score = 5
                writer.commit()
        return result
    monkeypatch.setattr(services, 'result_snapshot', interleave)
    saved = client.put(url, headers=headers, json=request)
    assert saved.status_code in (200, 409), saved.text
    if saved.status_code == 200:
        assert saved.json()['result'] == approved['after']
    else:
        with SessionLocal() as db:
            assert db.query(ManagementCorrection).count() == 0


def test_simultaneous_numeric_and_color_saves_never_overwrite_each_other(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import app.correction_service as service
    url, headers = setup(client)
    original_preview = service.preview_correction
    for revision in (0, 1):
        numeric = operation(client, url)
        color = dict(numeric, level='color', key='green', value=None)
        for request in (numeric, color):
            request['inputFingerprint'] = client.post(url + '/preview', headers=headers, json=request).json()['inputFingerprint']
        barrier = Barrier(2, timeout=10)
        def synchronize(*args, **kwargs):
            result = original_preview(*args, **kwargs)
            barrier.wait()
            return result
        with monkeypatch.context() as patch:
            patch.setattr(service, 'preview_correction', synchronize)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda request: client.put(url, headers=headers, json=request), (numeric, color)))
        assert sorted(response.status_code for response in results) == [200, 409]
        state = client.get(url).json()
        assert state['revision'] == revision + 1
        assert len(state['history']) == revision + 1


def test_corrections_cannot_be_read_or_written_from_another_workspace(client):
    from test_workspace_recovery import create_workspaces
    url, _ = setup(client)
    headers, (_, other) = create_workspaces(client)
    assert client.post(f'/api/platform/workspaces/{other["id"]}/select', headers=headers).status_code == 200
    admin_url = url.replace('/api/view/', '/api/admin/')
    assert client.get(admin_url).status_code == 404
    assert client.post(admin_url + '/preview', headers=headers, json={
        'revision': 0, 'configurationSignature': 'wrong', 'action': 'set',
        'level': 'activity', 'key': 'sport', 'value': 4,
    }).status_code in (403, 404)


def test_configuration_is_reloaded_inside_correction_transaction(client, monkeypatch):
    import app.routes_corrections as routes
    url, headers = setup(client)
    request = operation(client, url)
    original_definition = routes.published_definition
    def changed(*args):
        definition = original_definition(*args).model_copy(deep=True)
        definition.generalFactors[0].maximum += 1
        return definition
    monkeypatch.setattr(routes, 'published_definition', changed)
    assert client.post(url + '/preview', headers=headers, json=request).status_code == 409


def test_apply_preserves_every_original_submission_and_version(client):
    from sqlalchemy import select
    from app.models import EvaluationSubmission, SubmissionVersion, AdminEvaluation
    from test_viewer_performance_c1 import _populate_test_journeys
    session = _login(client)
    headers = {'X-CSRF-Token': session['csrfToken']}
    def originals(db):
        return {model.__tablename__: [dict(row) for row in db.execute(select(model.__table__).order_by(model.id)).mappings()]
                for model in (EvaluationSubmission, SubmissionVersion, AdminEvaluation)}
    with SessionLocal() as db:
        journey, _, _, recruit, *_ = _populate_test_journeys(db)
        before = originals(db)
    url = f'/api/view/journeys/{journey.id}/recruits/{recruit.id}/corrections'
    request = operation(client, url)
    request.update(key='escape_room', value=5)
    request['inputFingerprint'] = client.post(url + '/preview', headers=headers, json=request).json()['inputFingerprint']
    assert client.put(url, headers=headers, json=request).status_code == 200
    with SessionLocal() as db:
        assert originals(db) == before
