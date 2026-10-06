"""Read-only, workspace-scoped export for the manual Google Sheet backup.

No credentials, Google network calls, source writes or scoring formulas live here.
The checked-in inventory deliberately fails closed when the application schema changes.
"""
from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit, parse_qsl
from types import SimpleNamespace

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, object_storage
from .assessment_config import load_stored_definition
from .assessment_runtime import activate_assessment_definition, reset_assessment_definition
from .audit_presentation import present_events
from .tenant import select_system, reset_system
from scripts.database_transfer import encode, read_snapshot, assert_schema, TransferError


class BackupError(RuntimeError):
    """Safe-to-display error, with no source records or credentials."""


CHUNK_SIZE = 12000  # Even non-BMP Unicode stays below the Sheets cell limit.
MAX_EXPORT_BYTES = 64 * 1024 * 1024
EXCLUDED = {
    'platform_sessions', 'admin_sessions', 'user_sessions', 'evaluator_sessions',
    'recruit_attendance_sessions', 'recruit_attendance_access', 'idempotency_records',
}
SECRET = re.compile(r'password|secret|token|credential|authorization|apikey|accesskey', re.I)
URL = re.compile(r'(?:[a-z][a-z0-9+.-]*://|/)[^\s"<>]+', re.I)


def redact_url(match):
    original = match.group(0)
    decoded = unquote(original)
    try:
        parsed = urlsplit(decoded)
        sensitive = (parsed.scheme.lower() in ('postgres', 'postgresql', 'cockroachdb')
            or parsed.username is not None or parsed.password is not None
            or re.search(r'/(?:j|e|evaluate|attendance|recruit-attendance)/[^/]+', parsed.path, re.I)
            or any(SECRET.search(re.sub(r'[^a-z]', '', key.lower())) or key.lower() in ('key', 'signature')
                   or key.lower().startswith(('x-amz-', 'x-goog-')) for key, _ in parse_qsl(parsed.query)))
    except ValueError:
        sensitive = True
    return '[credential-bearing URL excluded]' if sensitive else original
TITLES = {
    'assessment_systems': 'Workspace', 'assessment_system_versions': 'Configuration history',
    'journeys': 'Journees', 'activity_states': 'Activity status', 'rubric_snapshots': 'Rubric history',
    'recruits': 'Recruits and attendance', 'recruit_directory': 'Recruit directory',
    'recruit_directory_state': 'Directory sync', 'evaluator_directory': 'Evaluator directory',
    'user_accounts': 'Accounts and access', 'platform_accounts': 'Linked accounts',
    'journey_permissions': 'Journee permissions', 'evaluators': 'Evaluator attendance',
    'mandatory_room_evaluators': 'Legacy mandatory rooms', 'activity_operations': 'Activity plans',
    'activity_evaluator_availability': 'Activity attendance', 'activity_mandatory_evaluators': 'Mandatory placements',
    'room_plans': 'Room versions', 'room_plan_recruits': 'Recruit rooms', 'room_plan_evaluators': 'Evaluator rooms',
    'assignment_rounds': 'Assignment versions', 'assignments': 'Assignments',
    'evaluation_submissions': 'Evaluations', 'admin_evaluations': 'Admin evaluations',
    'submission_versions': 'Evaluation history', 'management_corrections': 'Grade corrections',
    'general_assessments': 'General assessments', 'event_day_protections': 'Event protection',
    'audit_events': 'Audit records',
}


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def redact(value):
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items() if not SECRET.search(re.sub(r'[^a-z]', '', key.lower()))}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        # Audit payloads sometimes contain another serialized object as a string.
        if value.lstrip().startswith(('{', '[')):
            try:
                nested = json.loads(value)
                if isinstance(nested, (dict, list)):
                    return json_text(redact(nested))
            except (ValueError, RecursionError):
                pass
        return URL.sub(redact_url, value)
    return value


def clean_record(row):
    result = {}
    for key, value in row.items():
        if SECRET.search(key.replace('_', '')):
            continue
        if key == 'photo_data':
            result[key] = {'$photo': row['id']} if value or row.get('photo_object_key') else None
        elif key.endswith('_json') and isinstance(value, str):
            try:
                result[key] = json_text(redact(json.loads(value)))
            except json.JSONDecodeError:
                raise BackupError('Invalid stored JSON; backup stopped without discarding data.') from None
        else:
            result[key] = redact(encode(value))
    return result


def _inventory(connection):
    expected = json.loads(Path(__file__).with_name('sheet_backup_schema.json').read_text(encoding='utf-8'))
    actual = {t.name: list(t.c.keys()) for t in models.Base.metadata.tables.values()}
    if actual != expected or set(TITLES) | EXCLUDED != set(actual):
        raise BackupError('Backup schema inventory needs updating before this version can be exported.')
    try:
        assert_schema(connection)
    except TransferError:
        raise BackupError('Database schema differs from the backup schema; export stopped.') from None


def _read_records(connection, system_id):
    tables = models.Base.metadata.tables
    systems = tables['assessment_systems']
    system = connection.execute(select(systems).where(systems.c.id == system_id)).mappings().one_or_none()
    if system is None:
        raise BackupError('Workspace not found.')
    journeys = select(tables['journeys'].c.id).where(tables['journeys'].c.system_id == system_id)
    def children(table, key):
        return select(tables[table].c.id).where(tables[table].c[key].in_(journeys))
    recruits = children('recruits', 'journey_id')
    parents = {
        'room_plan_recruits': ('plan_id', children('room_plans', 'journey_id')),
        'room_plan_evaluators': ('plan_id', children('room_plans', 'journey_id')),
        'assignments': ('round_id', children('assignment_rounds', 'journey_id')),
        'submission_versions': ('submission_id', children('evaluation_submissions', 'journey_id')),
        'general_assessments': ('recruit_id', recruits),
    }
    accounts = tables['user_accounts']
    platform_ids = select(accounts.c.platform_account_id).where(accounts.c.system_id == system_id)
    data = {}
    for name in TITLES:
        table = tables[name]
        if name == 'assessment_systems':
            predicate = table.c.id == system_id
        elif name == 'platform_accounts':
            predicate = table.c.id.in_(platform_ids) | (table.c.id == system['owner_platform_account_id'])
        elif 'system_id' in table.c:
            predicate = table.c.system_id == system_id
        elif 'journey_id' in table.c:
            predicate = table.c.journey_id.in_(journeys)
        elif name in parents:
            key, ids = parents[name]
            predicate = table.c[key].in_(ids)
        else:
            raise BackupError('Unclassified table in backup schema.')
        data[name] = [dict(row) for row in connection.execute(select(table).where(predicate).order_by(*table.primary_key)).mappings()]
    return data


def build_export(engine, system_id):
    """Capture one consistent snapshot; release DB before accessing photo storage."""
    token = select_system(system_id)
    definition_token = None
    try:
        with read_snapshot(engine) as connection:
            captured_at = datetime.now(timezone.utc).isoformat()
            # sqlite3's legacy transaction mode does not BEGIN for SELECT.
            if connection.dialect.name == 'sqlite':
                connection.exec_driver_sql('BEGIN')
            _inventory(connection)
            raw = _read_records(connection, system_id)
            with Session(bind=connection) as db:
                system = raw['assessment_systems'][0]
                version = next((v for v in raw['assessment_system_versions'] if v['version'] == system['published_version']), None)
                definition_token = activate_assessment_definition(load_stored_definition(
                    json.loads(version['definition_json'] if version else system['draft_json'])))
                from .services import result_snapshot
                from .routes_viewer import _aggregate_results
                journeys = list(db.scalars(select(models.Journey).where(models.Journey.system_id == system_id)))
                snapshots = [(j, result_snapshot(db, j, include_criteria=True)) for j in journeys]
                completed = _aggregate_results([(j, s) for j, s in snapshots if j.status == 'completed'])
                results = [{**s, 'journeyId': j.id, 'journeyName': j.name} for j, s in snapshots]
                events = list(db.scalars(select(models.AuditEvent).where(models.AuditEvent.system_id == system_id).order_by(models.AuditEvent.created_at)))
                safe_events = []
                for event in events:
                    fields = {c.name: getattr(event, c.name) for c in models.AuditEvent.__table__.columns}
                    for key in ('before_json', 'after_json'):
                        fields[key] = json_text(redact(json.loads(fields[key] or '{}')))
                    safe_events.append(SimpleNamespace(**fields))
                readable_audit = redact(present_events(db, safe_events))
        records = {name: [clean_record(row) for row in rows] for name, rows in raw.items()}
        # Technical computed results preserve the actual application output too.
        records['_results'] = redact(results)
        records['_completed_results'] = [redact(completed)]
        photos = []
        total_bytes = len(json_text(records).encode('utf-8'))
        if total_bytes > MAX_EXPORT_BYTES:
            raise BackupError('Workspace exceeds the safe export size; nothing was truncated.')
        for recruit in raw['recruits']:
            data = recruit['photo_data']
            if recruit['photo_object_key']:
                try:
                    data = object_storage.get_photo(recruit['photo_object_key']) or data
                except Exception:
                    raise BackupError('A recruit photo could not be read; previous backup is unchanged.') from None
            if not data:
                if recruit['photo_object_key'] or recruit['photo_size']:
                    raise BackupError('A referenced recruit photo is missing; backup stopped.')
                continue
            data = bytes(data)
            digest = sha256(data).hexdigest()
            if (recruit['photo_sha256'] and recruit['photo_sha256'] != digest) or (recruit['photo_size'] is not None and recruit['photo_size'] != len(data)):
                raise BackupError('A recruit photo checksum or size does not match; backup stopped.')
            total_bytes += len(data) * 2
            if total_bytes > MAX_EXPORT_BYTES:
                raise BackupError('Workspace exceeds the safe export size for this server; nothing was truncated.')
            try:
                with Image.open(BytesIO(data)) as original:
                    preview = ImageOps.exif_transpose(original).convert('RGB')
                    preview.thumbnail((160, 160))
                    output = BytesIO()
                    preview.save(output, format='PNG')
            except Exception:
                raise BackupError('A recruit photo cannot be decoded for its preview; backup stopped.') from None
            photos.append({'recruitId': recruit['id'], 'name': recruit['name'], 'journeyId': recruit['journey_id'],
                'sha256': digest, 'size': len(data), 'contentType': recruit['photo_type'] or 'application/octet-stream',
                'data': base64.b64encode(data).decode('ascii'), 'preview': base64.b64encode(output.getvalue()).decode('ascii')})
        technical = []
        for table, rows in records.items():
            for index, row in enumerate(rows):
                content = json_text(row)
                parts = [content[i:i + CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE)]
                digest = sha256(content.encode('utf-8')).hexdigest()
                for part, chunk in enumerate(parts):
                    technical.append([table, str(index), str(part), str(len(parts)), digest, chunk])
        manifest = {'format': 'evalday-sheet-v1', 'schemaRevision': '0019_management_corrections',
            'workspaceId': system_id, 'workspaceName': system['name'], 'snapshotAt': captured_at,
            'counts': {name: len(rows) for name, rows in records.items()}, 'photoCount': len(photos),
            'recordsSha256': sha256(json_text(records).encode('utf-8')).hexdigest(),
            'excludedTables': sorted(EXCLUDED), 'excludedFields': ['passwords and hashes', 'authentication tokens and credential-bearing URLs'],
            'restoration': 'Account identities and permissions included. Set new passwords and access links after restoration.'}
        export = {'manifest': manifest, 'records': records, 'technical_rows': technical, 'photos': photos}
        export['tabs'] = readable_tabs(records, readable_audit, photos, manifest)
        if len(json_text(export).encode('utf-8')) > MAX_EXPORT_BYTES:
            raise BackupError('Workspace exceeds the safe export size; previous backup is unchanged.')
        return export
    finally:
        reset_assessment_definition(definition_token)
        reset_system(token)


def _value(value):
    if isinstance(value, dict) and set(value) == {'$type', 'value'}:
        return value['value']
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json_text(value)
    return str(value)


def _leaves(value, path=''):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f'{path} / {key}' if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, f'{path} / {index + 1}')
    else:
        yield path, _value(value)


def readable_tabs(records, readable_audit, photos, manifest):
    names = {row.get('id'): row.get('name') or row.get('username') for table in
        ('journeys', 'recruits', 'evaluators', 'user_accounts', 'recruit_directory', 'evaluator_directory', 'assessment_systems', 'platform_accounts')
        for row in records[table]}
    def context(row):
        return [names.get(row.get('journey_id'), ''), names.get(row.get('recruit_id'), '') or row.get('name', '') or row.get('username', ''), names.get(row.get('evaluator_id'), '')]
    tabs = [{'name': 'Backup summary', 'rows': [['Item', 'Value']] + [[key, value] for key, value in _leaves(manifest)] + [
        ['Privacy', 'Contains personal data and photos. Hidden tabs are not private.'],
        ['Reading', 'Named tabs contain readable data. Field details expands criteria, configuration and long notes.'],
        ['Restoration', 'Technical records and photo bytes are inside hidden tabs. No external photo link is needed.']]}]
    details = [['Section', 'Journee', 'Person', 'Evaluator', 'Record ID', 'Field / criterion', 'Part', 'Value']]
    for name, title in TITLES.items():
        rows = records[name]
        columns = [key for key in json.loads(Path(__file__).with_name('sheet_backup_schema.json').read_text())[name]
                   if not SECRET.search(key.replace('_', '')) and key != 'photo_data']
        # Name context first, technical IDs last.
        columns.sort(key=lambda c: (c == 'id' or c.endswith('_id'), c.endswith('_json')))
        rendered = [['Journee', 'Person', 'Evaluator'] + [c.removesuffix('_json').replace('_', ' ').capitalize() for c in columns]]
        for row in rows:
            ctx = context(row)
            record_id = row.get('id') or row.get('recruit_id', '')
            cells = []
            for column in columns:
                value = row.get(column)
                if column.endswith('_json'):
                    for field, scalar in _leaves(json.loads(value or '{}')):
                        for offset in range(0, max(1, len(scalar)), CHUNK_SIZE):
                            details.append([title, *ctx, record_id, field, str(offset // CHUNK_SIZE + 1), scalar[offset:offset + CHUNK_SIZE]])
                    cells.append('See Field details')
                else:
                    value = _value(value)
                    if len(value) > CHUNK_SIZE:
                        for offset in range(0, len(value), CHUNK_SIZE):
                            details.append([title, *ctx, record_id, column, str(offset // CHUNK_SIZE + 1), value[offset:offset + CHUNK_SIZE]])
                        value = 'See Field details (complete text)'
                    cells.append(value)
            rendered.append([*ctx, *cells])
        tabs.append({'name': title, 'rows': rendered})
    tabs.append({'name': 'Field details', 'rows': details})
    result_rows = [['Journee', 'Recruit', 'Overall score', 'Overall rank (completed)', 'Journee rank', 'Color', 'Recruit ID']]
    ranks = {row['profileKey']: row for row in records['_completed_results'][0]['rows']}
    for snapshot in records['_results']:
        for row in snapshot['rows']:
            ranked = ranks.get(f"{snapshot['journeyId']}:{row['recruitId']}", {})
            result_rows.append([snapshot['journeyName'], row['name'], _value(row['overallScore']),
                _value(ranked.get('overallRank')), _value(row.get('journeyRank', row.get('overallRank'))),
                _value(row.get('colorGrade', row.get('color'))), row['recruitId']])
            for field, scalar in _leaves(row):
                for offset in range(0, max(1, len(scalar)), CHUNK_SIZE):
                    details.append(['Calculated results', snapshot['journeyName'], row['name'], '', row['recruitId'],
                                    field, str(offset // CHUNK_SIZE + 1), scalar[offset:offset + CHUNK_SIZE]])
    tabs.append({'name': 'Results', 'rows': result_rows})
    audit_rows = [['Time (UTC)', 'Journee', 'Person', 'Account', 'Action', 'Field', 'Before', 'After', 'Reason', 'Event ID']]
    for event in readable_audit:
        for change in (event['changes'] + event['criteriaChanges']) or [{}]:
            audit_rows.append([event['createdAt'], event['journeyName'] or '', event['entityName'] or '', event['actorName'],
                event['title'], change.get('label', ''), _value(change.get('before')), _value(change.get('after')), event['reason'], event['id']])
    # Full audit data is already present in Field details and technical records.
    tabs.append({'name': 'Audit history', 'rows': [[s if len(str(s)) <= CHUNK_SIZE else 'See Audit records / Field details' for s in row] for row in audit_rows]})
    tabs.append({'name': 'Photos', 'rows': [['Preview', 'Recruit', 'Journee', 'Recruit ID', 'Content type', 'Bytes', 'SHA-256']] + [
        ['', p['name'], names.get(p['journeyId'], ''), p['recruitId'], p['contentType'], str(p['size']), p['sha256']] for p in photos]})
    return tabs


def reconstruct_records(rows):
    groups = defaultdict(list)
    for table, index, part, count, digest, chunk in rows:
        groups[(table, int(index))].append((int(part), int(count), digest, chunk))
    restored = {table: [] for table in [*TITLES, '_results', '_completed_results']}
    for (table, index), parts in sorted(groups.items()):
        parts.sort()
        if len(parts) != parts[0][1] or [p[0] for p in parts] != list(range(len(parts))):
            raise BackupError('Technical record chunks are incomplete.')
        content = ''.join(p[3] for p in parts)
        digest = sha256(content.encode('utf-8')).hexdigest()
        if any(p[2] != digest or p[1] != len(parts) for p in parts):
            raise BackupError('Technical record checksum mismatch.')
        if index != len(restored[table]):
            raise BackupError('Technical record ordering is invalid.')
        restored[table].append(json.loads(content))
    return restored


def _encode_operations(export):
    # Verify local reconstruction before constructing the upload protocol.
    if reconstruct_records(export['technical_rows']) != export['records']:
        raise BackupError('Technical reconstruction differs from the source snapshot.')
    tabs = list(export['tabs']) + [{'name': '_Records', 'hidden': True,
        'rows': [['Table', 'Record', 'Part', 'Parts', 'SHA-256', 'JSON chunk']] + export['technical_rows']}]
    photo_rows = [['Recruit ID', 'Part', 'Parts', 'SHA-256', 'Content type', 'Base64 chunk']]
    for photo in export['photos']:
        parts = [photo['data'][i:i + CHUNK_SIZE] for i in range(0, len(photo['data']), CHUNK_SIZE)]
        photo_rows.extend([[photo['recruitId'], str(i), str(len(parts)), photo['sha256'], photo['contentType'], part] for i, part in enumerate(parts)])
    tabs.append({'name': '_Photo bytes', 'hidden': True, 'rows': photo_rows})
    descriptors = [{'name': t['name'], 'rows': len(t['rows']), 'cols': len(t['rows'][0]), 'hidden': t.get('hidden', False)} for t in tabs]
    record_checks, chain, cursor = [], '', 0
    while cursor < len(export['technical_rows']):
        table, index, _, count, digest, _ = export['technical_rows'][cursor]
        item = [table, index, count, digest]
        chain = sha256((chain + sha256(json_text(item).encode('utf-8')).hexdigest()).encode('ascii')).hexdigest()
        record_checks.append(item)
        cursor += int(count)
    yield {'kind': 'prepare', 'tabs': descriptors, 'manifest': {**export['manifest'],
           'recordChain': chain, 'recordCount': len(record_checks)}}
    verifications = []
    for tab in tabs:
        start, batch, size = 1, [], 0
        for row in tab['rows']:
            row = [str(c) for c in row]
            n = len(json_text(row).encode('utf-8'))
            if batch and (size + n > 180_000 or len(batch) >= 1000):
                yield {'kind': 'rows', 'tab': tab['name'], 'start': start, 'rows': batch}
                verifications.append({'kind': 'verifyCells', 'tab': tab['name'], 'start': start,
                    'count': len(batch), 'sha256': sha256(json_text(batch).encode('utf-8')).hexdigest()})
                start += len(batch)
                batch, size = [], 0
            batch.append(row)
            size += n
        if batch:
            yield {'kind': 'rows', 'tab': tab['name'], 'start': start, 'rows': batch}
            verifications.append({'kind': 'verifyCells', 'tab': tab['name'], 'start': start,
                'count': len(batch), 'sha256': sha256(json_text(batch).encode('utf-8')).hexdigest()})
    for index, photo in enumerate(export['photos']):
        yield {'kind': 'image', 'row': index + 2, 'data': photo['preview'], 'sha256': sha256(base64.b64decode(photo['preview'])).hexdigest()}
    # Reconstruct and hash original binary, independently of display previews.
    for photo in export['photos']:
        yield {'kind': 'verifyPhoto', 'recruitId': photo['recruitId'], 'sha256': photo['sha256']}
    # Second read after all uploads: do not trust write-time acknowledgements.
    yield from verifications
    start = 2
    batch, size = [], 0
    for item in record_checks:
        count = int(item[2])
        if batch and (len(batch) >= 200 or size + count > 1000):
            yield {'kind': 'verifyRecords', 'start': start, 'records': batch}
            start += size
            batch, size = [], 0
        batch.append(item)
        size += count
    if batch:
        yield {'kind': 'verifyRecords', 'start': start, 'records': batch}
    yield {'kind': 'publish'}


def encode_operations(export):
    """Group retry-safe operations to avoid one Apps Script startup per small batch."""
    pending = []

    def grouped(operations):
        return operations[0] if len(operations) == 1 else {'kind': 'batch', 'operations': operations}

    for operation in _encode_operations(export):
        if operation['kind'] in ('prepare', 'publish'):
            if pending:
                yield grouped(pending)
                pending = []
            yield operation
            continue
        candidate = {'kind': 'batch', 'operations': [*pending, operation]}
        # The signed request is base64 encoded. Keep ample room below the
        # receiver's two-million-character envelope ceiling.
        if pending and (len(pending) >= 8 or len(json_text(candidate).encode('utf-8')) > 1_250_000):
            yield grouped(pending)
            pending = [operation]
        else:
            pending.append(operation)
    if pending:
        yield grouped(pending)
