from datetime import date, datetime, timezone
from sqlalchemy import select
from app.db import SessionLocal
from app.models import AuditEvent, Recruit, AssessmentSystem
from app.services import create_journey
from app.tenant import select_system, reset_system
from app.utils import audit, dumps, loads
from test_viewer_performance_c1 import _login


def seed(db):
    journey = create_journey(db, 'Archive day', date(2026, 9, 1), 1, 'Test')
    recruit = Recruit(journey_id=journey.id, name='Maya Example', present=True)
    db.add(recruit)
    db.flush()
    db.add(AuditEvent(journey_id=journey.id, actor_type='admin', actor_name='Alex Admin',
        action='management.correction', entity_type='recruit', entity_id=recruit.id,
        before_json=dumps({'overallScore': 6.636190476}), after_json=dumps({
            'overallScore': 4.92190476, 'operation': {'level': 'activity', 'key': 'skills', 'action': 'set'},
            'affectedCriteria': [{'activityKey': 'skills', 'key': 'posture',
                'before': {'name': 'Posture', 'effectiveAverage': 3, 'maximum': 5},
                'after': {'name': 'Posture', 'effectiveAverage': 4, 'maximum': 5}}]})))
    db.commit()
    return journey, recruit


def test_readable_legacy_corrections_and_scoped_search(client):
    _login(client)
    with SessionLocal() as db:
        journey, recruit = seed(db)
        journey.archived_at = datetime.now(timezone.utc)
        db.commit()
        original = db.scalar(select(AuditEvent).where(AuditEvent.action == 'management.correction')).after_json
    response = client.get('/api/admin/audit?search=Maya')
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert item['entityName'] == 'Maya Example'
    assert item['journeyName'] == 'Archive day'
    assert item['title'].startswith('Corrected')
    assert item['criteriaChanges'][0]['label'] == 'Posture'
    assert item['criteriaChanges'][0]['after'] == 4
    assert 'after' not in item  # No raw JSON/security fields on the workspace feed.
    assert client.get('/api/admin/audit?search=Alex').json()['items']
    assert client.get(f'/api/admin/audit?action=management.&journey_id={journey.id}').json()['items'][0]['id'] == item['id']
    assert not client.get('/api/admin/audit?search=NoMatch').json()['items']
    existing = client.get(f'/api/admin/journeys/{journey.id}/audit').json()
    assert any(e['entityName'] == 'Maya Example' for e in existing)
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == 'management.correction')).after_json == original


def test_snapshot_survives_rename_and_entity_removal(client):
    _login(client)
    with SessionLocal() as db:
        journey, recruit = seed(db)
        audit(db, journey_id=journey.id, actor_type='admin', actor_name='Alex', action='recruit.updated',
            entity_type='recruit', entity_id=recruit.id, after={'comment': 'Reviewed'})
        db.commit()
        db.delete(recruit)
        db.commit()
    items = client.get('/api/admin/audit?action=recruit.updated&search=Maya').json()['items']
    assert items[0]['entityName'] == 'Maya Example'


def test_pagination_dates_and_workspace_isolation(client):
    _login(client)
    with SessionLocal() as db:
        journey, recruit = seed(db)
        stamp = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        for i in range(7):
            db.add(AuditEvent(journey_id=journey.id, actor_type='admin', actor_name='Pagination',
                action='recruit.updated', entity_type='recruit', entity_id=recruit.id, created_at=stamp))
        other = AssessmentSystem(name='Private workspace', slug='private-audit', draft_json='{}', updated_by='Test')
        db.add(other)
        db.flush()
        token = select_system(other.id)
        try:
            hidden = create_journey(db, 'Private day', date(2026, 9, 1), 1, 'Private')
            db.commit()
        finally:
            reset_system(token)
    url = '/api/admin/audit?limit=2&search=Pagination&start=2026-09-01&end=2026-09-01'
    ids = []
    cursor = None
    while True:
        from urllib.parse import quote
        response = client.get(url + ('&cursor=' + quote(cursor) if cursor else ''))
        assert response.status_code == 200, response.text
        page = response.json()
        ids.extend(e['id'] for e in page['items'])
        cursor = page['nextCursor']
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 7
    assert client.get(f'/api/admin/audit?journey_id={hidden.id}').status_code == 404
    assert not client.get('/api/admin/audit?search=Private').json()['items']
    assert client.get('/api/admin/audit?start=2026-09-10&end=2026-09-01').status_code == 422
    assert client.get('/api/admin/audit?cursor=broken').status_code == 422
    assert not any(e['action'].startswith('account.') for e in client.get('/api/admin/audit').json()['items'])


def test_workspace_audit_requires_admin(client):
    assert client.get('/api/admin/audit').status_code == 401
