from datetime import date
from hashlib import sha256
from io import BytesIO
import json

import pytest
from PIL import Image
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import AssessmentSystem, AuditEvent, GeneralAssessment, Recruit
from app.services import create_journey
from app.sheet_backup_export import build_export, encode_operations, reconstruct_records, BackupError, redact
from test_viewer_performance_c1 import _login


def seed(client):
    _login(client)
    with SessionLocal() as db:
        system = db.scalar(select(AssessmentSystem))
        journey = create_journey(db, 'Completed day', date(2026, 9, 1), 1, 'Example')
        journey.status = 'completed'
        stream = BytesIO()
        Image.new('RGB', (24, 24), 'red').save(stream, format='WEBP')
        photo = stream.getvalue()
        recruit = Recruit(journey_id=journey.id, name='=Example شخص', present=True,
            photo_data=photo, photo_type='image/webp', photo_size=len(photo), photo_sha256=sha256(photo).hexdigest())
        db.add(recruit)
        db.flush()
        db.add(GeneralAssessment(recruit_id=recruit.id, notes='Long note: ' + 'ع😀' * 20000))
        db.add(AuditEvent(system_id=system.id, journey_id=journey.id, actor_type='admin', actor_name='Example',
            action='account.updated', entity_type='account', after_json=json.dumps({'managedPassword': 'DO-NOT-EXPORT', 'name': 'Visible'})))
        other = AssessmentSystem(name='Unrelated PRIVATE', slug='unrelated', draft_json='{}')
        db.add(other)
        db.commit()
        return system.id, recruit.id, photo


def test_complete_scoped_roundtrip(client):
    sid, rid, photo = seed(client)
    export = build_export(engine, sid)
    records = reconstruct_records(export['technical_rows'])
    assert records == export['records']
    assert records['recruits'][0]['name'] == '=Example شخص'
    assert records['general_assessments'][0]['notes'].endswith('ع😀' * 20000)
    packed = json.dumps(export, ensure_ascii=False)
    assert 'Unrelated PRIVATE' not in packed
    assert 'DO-NOT-EXPORT' not in packed
    assert 'test-password' not in packed
    assert 'password_hash' not in records['user_accounts'][0]
    assert 'public_token' not in records['journeys'][0]
    assert export['photos'][0]['sha256'] == sha256(photo).hexdigest()
    assert export['photos'][0]['recruitId'] == rid
    import base64
    assert base64.b64decode(export['photos'][0]['data']) == photo
    assert any(t['name'] == 'Results' for t in export['tabs'])
    operations = list(encode_operations(export))
    assert operations[-1]['kind'] == 'publish'
    assert all(len(json.dumps(op).encode()) < 1_500_000 for op in operations)


def test_unknown_schema_stops_export(client):
    sid, _, _ = seed(client)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE unexpected_backup_data (id TEXT)'))
    try:
        with pytest.raises(BackupError, match='schema'):
            build_export(engine, sid)
    finally:
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE unexpected_backup_data'))


def test_missing_and_corrupt_photos_fail(client, monkeypatch):
    sid, rid, _ = seed(client)
    with SessionLocal() as db:
        recruit = db.get(Recruit, rid)
        recruit.photo_object_key = 'missing.webp'
        recruit.photo_data = None
        db.commit()
    monkeypatch.setattr('app.object_storage.get_photo', lambda key: None)
    with pytest.raises(BackupError, match='photo'):
        build_export(engine, sid)
    monkeypatch.setattr('app.object_storage.get_photo', lambda key: b'wrong')
    with pytest.raises(BackupError, match='photo'):
        build_export(engine, sid)


def test_nested_redaction_preserves_grades():
    original = {'criteria': {'score': 5}, 'nested': {'csrfToken': 'secret', 'name': 'Sam'},
                'url': 'https://example.com/evaluate/secret-token',
                'connection': 'postgresql://user:password@example.com/db'}
    value = redact(original)
    assert value['criteria'] == {'score': 5}
    assert value['nested'] == {'name': 'Sam'}
    assert 'secret-token' not in json.dumps(value)
    assert 'password@' not in json.dumps(value)


def test_corrupt_technical_chunk_rejected(client):
    sid, _, _ = seed(client)
    export = build_export(engine, sid)
    export['technical_rows'][0][-1] += 'x'
    with pytest.raises(BackupError, match='checksum'):
        reconstruct_records(export['technical_rows'])
