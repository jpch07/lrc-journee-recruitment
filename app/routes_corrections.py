"""Identical correction operations with each surface's existing authorization."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from .auth import require_admin, require_results, require_csrf
from .db import get_db
from .schemas import ManagementCorrectionRequest
from .services import get_journey_or_404, get_recruit_or_404
from .correction_service import read_corrections, preview_correction, apply_correction


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
        journey, recruit = scope(db, journey_id, recruit_id)
        return preview_correction(db, journey, recruit, payload)

    @router.put(path)
    def apply(journey_id: str, recruit_id: str, payload: ManagementCorrectionRequest, request: Request,
              context=Depends(permission), db: Session = Depends(get_db)):
        require_csrf(request, context.csrf_token)
        journey, recruit = scope(db, journey_id, recruit_id)
        actor = context.actor_name if actor_type == 'admin' else context.username
        return apply_correction(db, journey, recruit, payload, actor, actor_type)

    return router


admin_router = make_router('/api/admin', require_admin, 'admin')
viewer_router = make_router('/api/view', require_results, 'results')
