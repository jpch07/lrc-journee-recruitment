"""Identical correction operations with each surface's existing authorization."""
from contextlib import contextmanager
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from .auth import require_admin, require_results, require_csrf
from .db import get_db
from .schemas import ManagementCorrectionRequest
from .services import get_journey_or_404, get_recruit_or_404
from .correction_service import read_corrections, preview_correction, apply_correction
from .assessment_service import published_definition
from .assessment_runtime import activate_assessment_definition, reset_assessment_definition
from .models import AssessmentSystem


@contextmanager
def correction_transaction(db, journey_id, recruit_id):
    """One snapshot for configuration, before/after, CAS and audit.

    End only the preceding read-only authentication transaction. Concurrent
    scoring writes after this snapshot logically follow this correction.
    Never retry a conflicting approved operation automatically.
    """
    if db.new or db.dirty or db.deleted:
        raise RuntimeError('Correction transaction cannot discard pending changes.')
    db.rollback()
    token = None
    try:
        if db.bind.dialect.name == 'postgresql':
            db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
        elif db.bind.dialect.name == 'sqlite':
            db.connection().exec_driver_sql('BEGIN')
        journey = get_journey_or_404(db, journey_id)
        recruit = get_recruit_or_404(db, journey.id, recruit_id)
        system = db.get(AssessmentSystem, journey.system_id)
        token = activate_assessment_definition(published_definition(db, system))
        yield journey, recruit
    except DBAPIError as exc:
        db.rollback()
        if (getattr(exc.orig, 'sqlstate', None) in {'40001', '40P01'} or
                getattr(exc.orig, 'sqlite_errorcode', None) in {5, 6, 517}):
            raise HTTPException(409, 'Grades changed while saving. Reload and preview again.') from exc
        raise
    finally:
        reset_assessment_definition(token)
        db.rollback()


def make_router(prefix, permission, actor_type):
    router = APIRouter(prefix=prefix, tags=['management-corrections'])
    path = '/journeys/{journey_id}/recruits/{recruit_id}/corrections'

    def scope(db, journey_id, recruit_id):
        journey = get_journey_or_404(db, journey_id)
        return journey, get_recruit_or_404(db, journey.id, recruit_id)

    @router.get(path)
    def read(journey_id: str, recruit_id: str, context=Depends(permission), db: Session = Depends(get_db)):
        journey, recruit = scope(db, journey_id, recruit_id)
        return read_corrections(db, journey, recruit)

    @router.post(path + '/preview')
    def preview(journey_id: str, recruit_id: str, payload: ManagementCorrectionRequest, request: Request,
                context=Depends(permission), db: Session = Depends(get_db)):
        require_csrf(request, context.csrf_token)
        with correction_transaction(db, journey_id, recruit_id) as (journey, recruit):
            return preview_correction(db, journey, recruit, payload)

    @router.put(path)
    def apply(journey_id: str, recruit_id: str, payload: ManagementCorrectionRequest, request: Request,
              context=Depends(permission), db: Session = Depends(get_db)):
        require_csrf(request, context.csrf_token)
        actor = context.actor_name if actor_type == 'admin' else context.username
        with correction_transaction(db, journey_id, recruit_id) as (journey, recruit):
            return apply_correction(db, journey, recruit, payload, actor, actor_type)

    return router


admin_router = make_router('/api/admin', require_admin, 'admin')
viewer_router = make_router('/api/view', require_results, 'results')
