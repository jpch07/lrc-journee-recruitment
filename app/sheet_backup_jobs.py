"""Ephemeral, browser-driven backup jobs. Remote leases fence other deployments."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import secrets
import tempfile
import threading
import time

from .db import engine
from .sheet_backup_export import BackupError, build_export, encode_operations, json_text


@dataclass
class Job:
    id: str
    system_id: str
    account_id: str
    created: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    file: object = None
    total: int = 0
    progress: int = 0
    state: str = 'preparing'
    message: str = 'Preparing a consistent workspace snapshot…'
    lock: object = field(default_factory=threading.Lock)

    def public(self):
        return {'jobId': self.id, 'state': self.state, 'progress': self.progress, 'total': self.total, 'message': self.message}

    def close(self):
        if self.file:
            self.file.close()
            self.file = None


_jobs: dict[str, Job] = {}
_guard = threading.Lock()


def _prune():
    for key, job in list(_jobs.items()):
        if time.monotonic() - job.touched > 900 and job.lock.acquire(blocking=False):
            try:
                job.close()
                del _jobs[key]
            finally:
                job.lock.release()


def clear_jobs():
    with _guard:
        for job in _jobs.values():
            job.close()
        _jobs.clear()


def find_job(job_id, system_id, account_id):
    with _guard:
        _prune()
        job = _jobs.get(job_id)
        if not job or job.system_id != system_id or job.account_id != account_id:
            raise BackupError('Backup job is unavailable after an interruption or restart. Start a new backup; the previous complete backup is unchanged.')
        return job


def active_job(system_id, account_id):
    with _guard:
        _prune()
        return next((j.public() for j in _jobs.values() if j.system_id == system_id and j.account_id == account_id
                     and j.state not in ('complete', 'cancelled')), None)


def start_job(system_id, account_id, receiver):
    with _guard:
        _prune()
        if any(j.system_id == system_id and j.state not in ('complete', 'cancelled') for j in _jobs.values()):
            raise BackupError('A backup is already running for this workspace.')
        job = Job(secrets.token_hex(16), system_id, account_id)
        job.lock.acquire()
        _jobs[job.id] = job
    try:
        receiver.call('begin', {'runId': job.id})
        export = build_export(engine, system_id)
        job.file = tempfile.TemporaryFile(mode='w+b')
        for op in encode_operations(export):
            job.file.write(json_text(op).encode('utf-8') + b'\n')
            job.total += 1
            if job.file.tell() > 128 * 1024 * 1024:
                raise BackupError('Backup exceeds this server’s safe temporary-file limit. Nothing was truncated.')
        job.file.seek(0)
        job.state, job.message = 'running', 'Snapshot ready. Uploading into the spreadsheet…'
        job.touched = time.monotonic()
        return job.public()
    except Exception:
        job.close()
        with _guard:
            _jobs.pop(job.id, None)
        try:
            receiver.call('abort', {'runId': job.id})
        except Exception:
            pass  # The remote lease expires; never mask the source failure.
        raise
    finally:
        job.lock.release()


def advance_job(job, receiver):
    if not job.lock.acquire(blocking=False):
        raise BackupError('This backup is already processing a step. Wait before retrying.')
    try:
        job.touched = time.monotonic()
        if job.state in ('complete', 'cancelled'):
            return job.public()
        if not job.file:
            raise BackupError('Backup snapshot is unavailable. Start a new backup.')
        offset = job.file.tell()
        line = job.file.readline()
        if not line:
            raise BackupError('Backup operation stream is incomplete; no publication was requested.')
        op = json.loads(line)
        try:
            result = receiver.call('apply', {'runId': job.id, 'sequence': job.progress, 'operation': op})
        except Exception:
            job.file.seek(offset)
            job.state, job.message = 'interrupted', 'Upload interrupted. Retry this backup to continue safely.'
            raise
        if op['kind'] == 'publish' and result.get('state') != 'complete':
            job.file.seek(offset)
            raise BackupError('Google did not confirm a complete publication. Retry to check its result.')
        if result.get('next', job.progress + 1) != job.progress + 1 and result.get('state') != 'complete':
            job.file.seek(offset)
            raise BackupError('Google acknowledged an unexpected backup sequence.')
        job.progress += 1
        job.state = 'complete' if op['kind'] == 'publish' else 'running'
        display_op = op['operations'][-1] if op['kind'] == 'batch' else op
        job.message = {
            'prepare': 'Preparing spreadsheet tabs…', 'rows': f"Backing up {display_op.get('tab', 'records')}…",
            'image': 'Embedding photo previews…', 'verifyPhoto': 'Verifying original photo bytes…',
            'verifyCells': 'Checking the stored spreadsheet data…', 'verifyRecords': 'Verifying complete record reconstruction…',
            'publish': 'Backup complete. Google read-back verification passed.',
        }.get(display_op['kind'], 'Backing up…')
        if job.state == 'complete':
            job.close()
        return job.public()
    finally:
        job.lock.release()


def cancel_job(job, receiver):
    if not job.lock.acquire(blocking=False):
        raise BackupError('A backup step is still processing. Wait before cancelling.')
    try:
        if job.state != 'complete':
            receiver.call('abort', {'runId': job.id})
            job.close()
            job.state, job.message = 'cancelled', 'Backup cancelled. The previous complete backup is unchanged.'
        return job.public()
    finally:
        job.lock.release()
