from datetime import date
from hashlib import sha256
from io import BytesIO
import json
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest
from PIL import Image
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import (AssessmentSystem, AuditEvent, GeneralAssessment, Recruit,
                        Journey, RoomPlan, RoomPlanRecruit, PlatformAccount, UserAccount)
from app.services import create_journey
from app.google_sheet_presentations import build_presentation_tabs
from app.tenant import select_system, reset_system
from app.sheet_backup_export import build_export, encode_operations, reconstruct_records, BackupError, redact, json_text
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
        db.flush()
        other_day = Journey(system_id=other.id, name='PRIVATE day', event_date=date(2026, 9, 2), public_token='private-test-link')
        db.add(other_day)
        db.flush()
        other_person = Recruit(journey_id=other_day.id, name='PRIVATE recruit')
        other_plan = RoomPlan(journey_id=other_day.id, version=1, seed='PRIVATE seed', created_by='Other')
        unrelated_account = PlatformAccount(username='PRIVATE account', password_hash='private-test-hash')
        linked_account = PlatformAccount(username='Linked account', password_hash='linked-test-hash')
        db.add_all([other_person, other_plan, unrelated_account, linked_account])
        db.flush()
        db.add_all([RoomPlanRecruit(plan_id=other_plan.id, recruit_id=other_person.id, room_number=1),
                    GeneralAssessment(recruit_id=other_person.id, notes='PRIVATE assessment')])
        account = db.scalar(select(UserAccount).where(UserAccount.system_id == system.id))
        account.platform_account_id = linked_account.id
        db.commit()
        return system.id, recruit.id, photo


def flattened(operations):
    for operation in operations:
        yield from operation.get('operations', [operation])


def test_complete_scoped_roundtrip(client):
    sid, rid, photo = seed(client)
    export = build_export(engine, sid)
    records = reconstruct_records(export['technical_rows'])
    assert records == export['records']
    assert records['recruits'][0]['name'] == '=Example شخص'
    assert records['general_assessments'][0]['notes'].endswith('ع😀' * 20000)
    packed = json.dumps(export, ensure_ascii=False)
    assert 'Unrelated PRIVATE' not in packed
    assert 'PRIVATE' not in packed
    assert {r['username'] for r in records['platform_accounts']} == {'JP Chaaya', 'Linked account'}
    assert records['room_plan_recruits'] == []
    assert 'DO-NOT-EXPORT' not in packed
    assert 'test-password' not in packed
    assert 'password_hash' not in records['user_accounts'][0]
    assert 'public_token' not in records['journeys'][0]
    assert export['photos'][0]['sha256'] == sha256(photo).hexdigest()
    assert export['photos'][0]['recruitId'] == rid
    import base64
    assert base64.b64decode(export['photos'][0]['data']) == photo
    assert [tab['name'] for tab in export['tabs'][:3]] == ['Results', 'Recruit Profiles', 'Backup summary']
    assert [tab.get('presentation') for tab in export['tabs'][:2]] == ['results-v2', 'recruit-profiles-v2']
    assert all(tab['name'] != 'Results' for tab in export['tabs'][2:])
    operations = list(encode_operations(export))
    assert operations[-1]['kind'] == 'publish'
    assert all(len(json_text(op).encode()) < 1_300_000 for op in operations)
    assert all(len(op.get('operations', [])) <= 8 for op in operations)
    assert all(op['kind'] != 'batch' or all(child['kind'] not in ('prepare', 'publish', 'batch')
               for child in op['operations']) for op in operations)
    flattened_operations = list(flattened(operations))
    assert [op['kind'] for op in flattened_operations[-5:]] == [
        'layout', 'verifyLayout', 'layout', 'verifyLayout', 'publish',
    ]
    assert all(op['kind'] != 'batch' for op in operations if op['kind'] in ('layout', 'verifyLayout'))
    prepare = flattened_operations[0]
    presentation_descriptors = [tab for tab in prepare['tabs'] if tab.get('presentation')]
    assert all(tab['cols'] <= 128 for tab in presentation_descriptors)
    assert all(tab['cols'] <= 60 for tab in prepare['tabs'] if not tab.get('presentation'))
    assert all('Same Name' not in json.dumps(op) for op in flattened_operations if op['kind'] == 'layout')


def test_technical_results_keep_every_journee_and_full_completed_aggregate(client):
    sid, _, _ = seed(client)
    token = select_system(sid)
    try:
        with SessionLocal() as db:
            active = create_journey(db, 'Active technical day', date(2026, 9, 3), 1, 'Example')
            active.status = 'active'
            db.add(Recruit(journey_id=active.id, name='Active technical recruit', present=True))
            db.commit()
    finally:
        reset_system(token)
    export = build_export(engine, sid)
    assert {row['journeyName'] for row in export['records']['_results']} == {
        'Completed day', 'Active technical day',
    }
    completed = export['records']['_completed_results'][0]
    assert {'formula', 'scoreMaximum', 'dimensionNames', 'performanceBands',
            'dimensionAverages', 'activityAverages', 'rows'} <= set(completed)
    assert 'Active technical day' not in export['presentation_payload']['results']['scopes']


def test_post_snapshot_photo_lookup_cannot_change_presentation_or_technical_results(client, monkeypatch):
    sid, rid, photo = seed(client)
    with SessionLocal() as db:
        recruit = db.get(Recruit, rid)
        recruit.photo_object_key = 'snapshot-photo.webp'
        db.commit()

    def mutate_after_snapshot(_key):
        with SessionLocal() as db:
            recruit = db.get(Recruit, rid)
            recruit.name = 'Mutated after snapshot'
            assessment = db.get(GeneralAssessment, rid)
            assessment.values_json = json.dumps({'punctuality': 1, 'respect': 1, 'seriousness': 1})
            db.commit()
        return photo

    monkeypatch.setattr('app.object_storage.get_photo', mutate_after_snapshot)
    export = build_export(engine, sid)
    technical_row = export['records']['_results'][0]['rows'][0]
    profile_rows = export['presentation_payload']['profiles']['summaries']
    assert technical_row['name'] == '=Example شخص'
    assert {row['name'] for row in profile_rows} == {'=Example شخص'}
    assert {row['overallScore'] for row in profile_rows} == {technical_row['overallScore']}


def test_real_export_protocol_runs_through_google_simulator(client):
    sid, _, _ = seed(client)
    operations = list(encode_operations(build_export(engine, sid)))
    script = r'''
      const fs=require('node:fs'),assert=require('node:assert/strict');
      const {fakeGoogle}=require('./tests/sheet_backup_receiver.test.cjs');
      const receiver=require('./integrations/google-sheet-backup/Code.js');
      const env=fakeGoogle(),runId='b'.repeat(32);
      const operations=JSON.parse(fs.readFileSync(0,'utf8'));
      receiver.dispatch({action:'begin',runId},env.props,1000);
      let result;
      operations.forEach((operation,sequence)=>{
        result=receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000);
      });
      assert.equal(result.state,'complete');
      const state=JSON.parse(env.props.getProperty('RUN'));
      assert.deepEqual(state.tabs.slice(0,3).map(tab=>tab.name),['Results','Recruit Profiles','Backup summary']);
      const results=env.ss.getSheetByName('Results'),profiles=env.ss.getSheetByName('Recruit Profiles');
      assert.ok(results.getRange('A6').getFormula());
      assert.ok(results.getRange('B3').getDataValidation());
      assert.ok(results.getRange('E3').getDataValidation());
      assert.equal(results.getCharts().length,0);
      assert.equal(results.getProtections()[0].getDescription(),'Evalday interactive presentation selectors');
      assert.deepEqual(results.getProtections()[0].unprotected.sort(),['B3','E3']);
      assert.equal(profiles.getCharts().length,2);
      assert.equal(profiles.getImages().length,1);
      assert.equal(profiles.getProtections()[0].getDescription(),'Evalday interactive presentation selectors');
      assert.ok(env.ss.getSheetByName('Backup - Backup summary'));
      assert.equal(env.ss.getSheetByName('Backup - Photos').images.length,1);
      assert.ok(env.ss.getSheetByName('My own notes'));
    '''
    result = subprocess.run(['node', '-e', script], input=json.dumps(operations, ensure_ascii=False),
        text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stdout + result.stderr


def test_empty_completed_profile_export_runs_through_google_simulator(client):
    _login(client)
    with SessionLocal() as db:
        sid = db.scalar(select(AssessmentSystem)).id
    operations = list(encode_operations(build_export(engine, sid)))
    script = r'''
      const fs=require('node:fs'),assert=require('node:assert/strict');
      const {fakeGoogle}=require('./tests/sheet_backup_receiver.test.cjs');
      const receiver=require('./integrations/google-sheet-backup/Code.js');
      const env=fakeGoogle(),runId='b'.repeat(32),operations=JSON.parse(fs.readFileSync(0,'utf8'));
      receiver.dispatch({action:'begin',runId},env.props,1000);
      let result;
      operations.forEach((operation,sequence)=>result=receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000));
      assert.equal(result.state,'complete');
      const profiles=env.ss.getSheetByName('Recruit Profiles');
      assert.equal(profiles.getRange('E3').getValue(),'');
      assert.equal(profiles.getRange('E3').getDataValidation(),null);
      assert.equal(profiles.getImages().length,0);
    '''
    result = subprocess.run(['node', '-e', script], input=json.dumps(operations, ensure_ascii=False),
        text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('factor_count', [0, 2, 3])
def test_python_generated_dynamic_factor_layout_runs_through_google_simulator(client, factor_count):
    sid, _, _ = seed(client)
    export = build_export(engine, sid)
    payload = deepcopy(export['presentation_payload'])
    factors = payload['profiles']['generalFactors'][:factor_count]
    payload['profiles']['generalFactors'] = factors
    payload['definition']['generalFactors'] = deepcopy(factors)
    export['tabs'][:2] = build_presentation_tabs(payload, export['photos'])
    operations = list(encode_operations(export))
    script = r'''
      const fs=require('node:fs'),assert=require('node:assert/strict');
      const {fakeGoogle}=require('./tests/sheet_backup_receiver.test.cjs');
      const receiver=require('./integrations/google-sheet-backup/Code.js');
      const env=fakeGoogle(),runId='a'.repeat(32),operations=JSON.parse(fs.readFileSync(0,'utf8'));
      receiver.dispatch({action:'begin',runId},env.props,1000);
      let result;
      operations.forEach((operation,sequence)=>result=receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000));
      assert.equal(result.state,'complete');
    '''
    result = subprocess.run(['node', '-e', script], input=json.dumps(operations, ensure_ascii=False),
        text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_export_retries_same_failed_profile_layout_without_replacing_previous_backup(client):
    sid, _, _ = seed(client)
    operations = list(encode_operations(build_export(engine, sid)))
    script = r'''
      const fs=require('node:fs'),assert=require('node:assert/strict');
      const {fakeGoogle}=require('./tests/sheet_backup_receiver.test.cjs');
      const receiver=require('./integrations/google-sheet-backup/Code.js');
      const env=fakeGoogle(),operations=JSON.parse(fs.readFileSync(0,'utf8'));
      function run(runId,injectFailure=false) {
        receiver.dispatch({action:'begin',runId},env.props,1000);
        let failed=false;
        for(let sequence=0;sequence<operations.length;sequence++) {
          const operation=operations[sequence];
          if(injectFailure && !failed && operation.kind==='layout' && operation.presentation==='recruit-profiles-v2') {
            const state=JSON.parse(env.props.getProperty('RUN'));
            const tab=state.tabs.find(item=>item.name==='Recruit Profiles');
            const staged=env.ss.getSheetById(tab.id),original=staged.newChart;
            staged.newChart=()=>{throw new Error('injected layout failure')};
            assert.throws(()=>receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000),/injected layout failure/);
            staged.newChart=original;
            assert.ok(env.ss.getSheetByName('Results'));
            assert.equal(JSON.parse(env.ss.getDeveloperMetadata().find(item=>item.getKey()==='evalday_backup_published').getValue()).runId,'1'.repeat(32));
            receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000);
            failed=true;
          } else receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000);
        }
        return failed;
      }
      assert.equal(run('1'.repeat(32)),false);
      const oldResults=env.ss.getSheetByName('Results').getSheetId();
      assert.equal(run('2'.repeat(32),true),true);
      assert.notEqual(env.ss.getSheetByName('Results').getSheetId(),oldResults);
      const profiles=env.ss.getSheetByName('Recruit Profiles');
      assert.equal(profiles.getCharts().length,2);
      assert.equal(profiles.getImages().length,1);
      assert.equal(profiles.getProtections().filter(item=>item.getDescription()==='Evalday interactive presentation selectors').length,1);
      assert.equal(JSON.parse(env.ss.getDeveloperMetadata().find(item=>item.getKey()==='evalday_backup_published').getValue()).runId,'2'.repeat(32));
      assert.ok(env.ss.getSheetByName('My own notes'));
    '''
    result = subprocess.run(['node', '-e', script], input=json.dumps(operations, ensure_ascii=False),
        text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_export_cells_fit_google_limit_even_for_long_result_notes(client):
    sid, _, _ = seed(client)
    export = build_export(engine, sid)
    for op in flattened(encode_operations(export)):
        if op['kind'] == 'rows':
            assert all(len(cell.encode('utf-16-le')) // 2 < 49000 for row in op['rows'] for cell in row), op['tab']


def test_credential_urls_and_nested_json_strings_are_redacted():
    value = redact({'note': '{"managedPassword":"nested-secret","name":"Visible"}',
                    'source': 'https://user:basic-secret@example.test/sheet',
                    'public': 'https://evalday.example/j/session-secret',
                    'relative': '/lrc/attendance/relative-secret',
                    'encoded': 'https://example.test/photo?%74oken=encoded-secret',
                    'signed': 'https://storage.example/photo?X-Goog-Signature=signature-secret'})
    text = json.dumps(value)
    for secret in ('nested-secret','basic-secret','session-secret','signature-secret','relative-secret','encoded-secret'):
        assert secret not in text
    assert 'Visible' in text


def test_corrupt_technical_chunk_rejected(client):
    sid, _, _ = seed(client)
    export = build_export(engine, sid)
    export['technical_rows'][0][-1] += 'x'
    with pytest.raises(BackupError, match='checksum'):
        reconstruct_records(export['technical_rows'])


def test_readable_audit_is_redacted_too(client):
    sid, _, _ = seed(client)
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == 'account.updated'))
        event.reason = 'Reference: https://example.test/evaluate/AUDIT-SECRET'
        event.after_json = json.dumps({'comment':'https://example.test/evaluate/COMMENT-SECRET'})
        db.commit()
    export = build_export(engine, sid)
    assert 'AUDIT-SECRET' not in json.dumps(export)
    assert 'COMMENT-SECRET' not in json.dumps(export)


def test_export_preserves_original_evaluations_history_and_effective_corrections(client):
    from decimal import Decimal
    from app.models import (Evaluator, AssignmentRound, Assignment, EvaluationSubmission, SubmissionVersion)
    from test_management_correction_api import setup, operation
    from scripts.database_transfer import decode
    url, headers = setup(client)
    with SessionLocal() as db:
        day = db.scalar(select(Journey))
        recruit = db.scalar(select(Recruit))
        assessor = Evaluator(journey_id=day.id, name='Original evaluator')
        round_ = AssignmentRound(journey_id=day.id, activity_code='sport', version=1, seed='test', created_by='Test')
        db.add_all([assessor, round_])
        db.flush()
        task = Assignment(round_id=round_.id, evaluator_id=assessor.id, recruit_id=recruit.id)
        db.add(task)
        db.flush()
        submission = EvaluationSubmission(assignment_id=task.id, journey_id=day.id, activity_code='sport',
            evaluator_id=assessor.id, recruit_id=recruit.id, status='submitted', score=Decimal(3),
            comments='Original evaluation', raw_payload_json='{"original":42}')
        db.add(submission)
        db.flush()
        db.add(SubmissionVersion(submission_id=submission.id, version=1, payload_json='{"historical":true}',
                                 score=Decimal(3), actor_type='evaluator', actor_name=assessor.name))
        db.commit()
        sid = day.system_id
    request = operation(client, url)
    request['inputFingerprint'] = client.post(url + '/preview', headers=headers, json=request).json()['inputFingerprint']
    assert client.put(url, headers=headers, json=request).status_code == 200
    export = build_export(engine, sid)
    records = reconstruct_records(export['technical_rows'])
    assert records == export['records']
    assert decode(records['evaluation_submissions'][0]['score']) == Decimal(3)
    assert records['evaluation_submissions'][0]['raw_payload_json'] == '{"original":42}'
    assert records['submission_versions'][0]['payload_json'] == '{"historical":true}'
    assert len(records['management_corrections']) == 1
    assert any(row['action'] == 'management.correction' for row in records['audit_events'])
    sport = records['_results'][0]['rows'][0]['activities']['sport']
    assert sport['score'] == 4 and sport['automaticScore'] == 2
    assert all(decode(row['score']) == Decimal(2) for row in records['admin_evaluations'])
