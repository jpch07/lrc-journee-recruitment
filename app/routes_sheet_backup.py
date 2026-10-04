"""Owner-only manual backup endpoints. No credentials or export data go to JS."""
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth import UserContext, require_owner, require_csrf
from .db import get_db, SessionLocal
from .sheet_backup_transport import Receiver, SPREADSHEET_URL, BackupError
from .sheet_backup_jobs import start_job, advance_job, cancel_job, find_job, active_job
from .utils import audit

router = APIRouter(prefix='/api/admin/sheet-backup', tags=['workspace backup'])


def _receiver(context):
    # Deliberately opt-in by stable workspace ID: never send a different tenant
    # to this spreadsheet because a name or cookie changed.
    if os.getenv('LRC_SHEET_BACKUP_WORKSPACE_ID', '') != context.system_id:
        raise BackupError('Google backup is not configured for this workspace.')
    return Receiver(os.getenv('LRC_SHEET_BACKUP_URL', ''), os.getenv('LRC_SHEET_BACKUP_SECRET', ''), workspace_id=context.system_id)


def _audit(context, action, job):
    # Backup integrity does not depend on the separate audit write succeeding.
    # Never record source values, receiver URL or credentials in the audit.
    try:
        with SessionLocal() as db:
            audit(db, journey_id=None, actor_type='admin', actor_name=context.username,
                  action=action, entity_type='workspace', entity_id=context.system_id,
                  after={'backupJob':job['jobId'], 'state':job['state']})
            db.commit()
    except Exception:
        pass


@router.get('')
def status(context: UserContext = Depends(require_owner), db: Session = Depends(get_db)):
    db.close()  # Release the auth connection before waiting on Google.
    base = {'spreadsheetUrl': SPREADSHEET_URL, 'workspaceId':context.system_id, 'configured': False, 'connected': False, 'job': active_job(context.system_id, context.account_id)}
    try:
        receiver = _receiver(context)
    except BackupError as exc:
        return {**base, 'message':str(exc)}
    try:
        remote = receiver.call('status', {})
        return {**base, 'configured':True, 'connected':True, **remote}
    except BackupError as exc:
        return {**base, 'configured':True, 'message':str(exc)}


@router.post('/start')
def start(request: Request, context: UserContext = Depends(require_owner), db: Session = Depends(get_db)):
    require_csrf(request, context.csrf_token)
    db.close()
    try:
        result = start_job(context.system_id, context.account_id, _receiver(context))
    except BackupError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(502, 'The workspace snapshot could not be completed. No partial backup was published.') from None
    _audit(context, 'workspace.backup_started', result)
    return result


@router.post('/{job_id}/advance')
def advance(job_id: str, request: Request, context: UserContext = Depends(require_owner), db: Session = Depends(get_db)):
    require_csrf(request, context.csrf_token)
    db.close()
    job, was_interrupted = None, False
    try:
        job = find_job(job_id, context.system_id, context.account_id)
        was_complete = job.state == 'complete'
        was_interrupted = job.state == 'interrupted'
        result = advance_job(job, _receiver(context))
    except BackupError as exc:
        if job and job.state == 'interrupted' and not was_interrupted:
            _audit(context, 'workspace.backup_interrupted', job.public())
        raise HTTPException(502, str(exc)) from None
    except Exception:
        raise HTTPException(502, 'Backup upload was interrupted. Retry; the previous complete backup remains available.') from None
    if result['state'] == 'complete' and not was_complete:
        _audit(context, 'workspace.backup_completed', result)
    return result


@router.post('/{job_id}/cancel')
def cancel(job_id: str, request: Request, context: UserContext = Depends(require_owner), db: Session = Depends(get_db)):
    require_csrf(request, context.csrf_token)
    db.close()
    try:
        result = cancel_job(find_job(job_id, context.system_id, context.account_id), _receiver(context))
    except BackupError as exc:
        raise HTTPException(409, str(exc)) from None
    _audit(context, 'workspace.backup_cancelled', result)
    return result
