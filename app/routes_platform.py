from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .assessment_config import blank_assessment_definition
from .assessment_service import ensure_unique_system_name, system_slug
from .auth import (
    PLATFORM_COOKIE,
    SYSTEM_COOKIE,
    USER_COOKIE,
    PlatformContext,
    _token_hash,
    clear_expired_sessions,
    clear_login_attempts,
    create_platform_session,
    create_user_session,
    enforce_login_rate_limit,
    hash_password,
    require_csrf,
    require_platform,
    set_system_cookie,
    verify_password,
)
from .db import get_db
from .models import (
    AssessmentSystem,
    AssessmentSystemVersion,
    EvaluatorDirectory,
    Journey,
    PlatformAccount,
    PlatformSession,
    UserAccount,
    UserSession,
)
from .utils import dumps


router = APIRouter(prefix="/api/platform", tags=["platform"])


class PlatformCredentials(BaseModel):
    username: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=8, max_length=500)

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        return " ".join(value.split())


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class WorkspaceDeleteRequest(BaseModel):
    confirmation_name: str = Field(min_length=2, max_length=200)


def _account(db: Session, account_id: str) -> PlatformAccount:
    account = db.scalar(
        select(PlatformAccount)
        .where(PlatformAccount.id == account_id)
        .execution_options(bypass_recruitment_scope=True)
    )
    if not account or not account.active:
        raise HTTPException(status_code=401, detail="Account is no longer active.")
    return account


def _platform_payload(account: PlatformAccount, csrf_token: str) -> dict:
    return {
        "id": account.id,
        "username": account.username,
        "csrfToken": csrf_token,
    }


def _owned_workspace(db: Session, account_id: str, system_id: str) -> AssessmentSystem:
    system = db.scalar(
        select(AssessmentSystem)
        .where(
            AssessmentSystem.id == system_id,
            AssessmentSystem.owner_platform_account_id == account_id,
        )
        .execution_options(bypass_recruitment_scope=True)
    )
    if not system:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return system


def _unique_slug(db: Session, name: str) -> str:
    base = system_slug(name)
    candidate = base
    suffix = 2
    while db.scalar(
        select(AssessmentSystem.id)
        .where(func.lower(AssessmentSystem.slug) == candidate.casefold())
        .execution_options(bypass_recruitment_scope=True)
    ):
        candidate = f"{base[:86]}-{suffix}"
        suffix += 1
    return candidate


@router.post("/register")
def register(
    payload: PlatformCredentials,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    enforce_login_rate_limit(request)
    duplicate = db.scalar(
        select(PlatformAccount.id)
        .where(func.lower(PlatformAccount.username) == payload.username.casefold())
        .execution_options(bypass_recruitment_scope=True)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="This username is already in use.")
    account = PlatformAccount(username=payload.username, password_hash=hash_password(payload.password))
    db.add(account)
    db.flush()
    clear_expired_sessions(db)
    session = create_platform_session(db, response, account)
    clear_login_attempts(request)
    db.commit()
    return _platform_payload(account, session.csrf_token)


@router.post("/login")
def login(
    payload: PlatformCredentials,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    enforce_login_rate_limit(request)
    account = db.scalar(
        select(PlatformAccount)
        .where(func.lower(PlatformAccount.username) == payload.username.casefold())
        .execution_options(bypass_recruitment_scope=True)
    )
    if not account or not account.active or not verify_password(account.password_hash, payload.password):
        raise HTTPException(status_code=403, detail="Invalid username or password.")
    clear_expired_sessions(db)
    session = create_platform_session(db, response, account)
    clear_login_attempts(request)
    db.commit()
    return _platform_payload(account, session.csrf_token)


@router.get("/session")
def session(context: PlatformContext = Depends(require_platform), db: Session = Depends(get_db)):
    return _platform_payload(_account(db, context.account_id), context.csrf_token)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    context: PlatformContext = Depends(require_platform),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    db.execute(delete(PlatformSession).where(PlatformSession.token_hash == context.token_hash))
    workspace_token = request.cookies.get(USER_COOKIE, "")
    if workspace_token:
        db.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(workspace_token)))
    response.delete_cookie(PLATFORM_COOKIE, path="/")
    response.delete_cookie(USER_COOKIE, path="/")
    response.delete_cookie(SYSTEM_COOKIE, path="/")
    response.delete_cookie("lrc_attendance_journey", path="/")
    db.commit()
    return {"ok": True}


@router.get("/workspaces")
def workspaces(context: PlatformContext = Depends(require_platform), db: Session = Depends(get_db)):
    systems = list(db.scalars(
        select(AssessmentSystem)
        .where(AssessmentSystem.owner_platform_account_id == context.account_id)
        .order_by(AssessmentSystem.updated_at.desc(), func.lower(AssessmentSystem.name))
        .execution_options(bypass_recruitment_scope=True)
    ))
    result = []
    for system in systems:
        statuses = dict(db.execute(
            select(Journey.status, func.count(Journey.id))
            .where(Journey.system_id == system.id)
            .group_by(Journey.status)
            .execution_options(bypass_recruitment_scope=True)
        ).all())
        result.append({
            "id": system.id,
            "name": system.name,
            "slug": system.slug,
            "status": system.status,
            "journeyCount": sum(statuses.values()),
            "activeJourneyCount": statuses.get("active", 0),
            "completedJourneyCount": statuses.get("completed", 0),
            "updatedAt": system.updated_at.isoformat(),
        })
    return result


@router.post("/workspaces")
def create_workspace(
    payload: WorkspaceCreateRequest,
    request: Request,
    context: PlatformContext = Depends(require_platform),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    platform = _account(db, context.account_id)
    try:
        ensure_unique_system_name(db, None, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="A workspace with this name already exists.") from exc
    definition = blank_assessment_definition().model_copy(update={"name": payload.name})
    definition_json = dumps(definition.model_dump(mode="json"))
    system = AssessmentSystem(
        id=str(uuid.uuid4()),
        name=payload.name,
        slug=_unique_slug(db, payload.name),
        owner_platform_account_id=platform.id,
        draft_json=definition_json,
        published_version=1,
        updated_by=platform.username,
    )
    db.add(system)
    db.flush()
    db.add(AssessmentSystemVersion(
        system_id=system.id,
        version=1,
        definition_json=definition_json,
        change_summary="Initial workspace configuration.",
        published_by=platform.username,
    ))
    category = definition.assessors.categories[0].key
    directory = EvaluatorDirectory(
        system_id=system.id,
        name=platform.username,
        default_role=category,
        active=True,
    )
    db.add(directory)
    db.flush()
    owner = UserAccount(
        system_id=system.id,
        platform_account_id=platform.id,
        username=platform.username,
        password_hash=platform.password_hash,
        directory_id=directory.id,
        evaluator_role=category,
        is_owner=True,
        can_admin=True,
        can_results=True,
        can_evaluate=True,
        active=True,
        must_change_password=False,
    )
    db.add(owner)
    db.flush()
    db.commit()
    return {
        "id": system.id,
        "name": system.name,
        "slug": system.slug,
        "journeyCount": 0,
    }


@router.delete("/workspaces/{system_id}")
def delete_workspace(
    system_id: str,
    payload: WorkspaceDeleteRequest,
    request: Request,
    response: Response,
    context: PlatformContext = Depends(require_platform),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    system = _owned_workspace(db, context.account_id, system_id)
    if payload.confirmation_name != system.name:
        raise HTTPException(status_code=422, detail="Enter the exact workspace name to confirm deletion.")
    selected_slug = request.cookies.get(SYSTEM_COOKIE, "")
    deleted = {"id": system.id, "name": system.name, "slug": system.slug}
    db.delete(system)
    db.commit()
    if selected_slug.casefold() == deleted["slug"].casefold():
        response.delete_cookie(USER_COOKIE, path="/")
        response.delete_cookie(SYSTEM_COOKIE, path="/")
        response.delete_cookie("lrc_attendance_journey", path="/")
    return {"deleted": deleted}


@router.post("/workspaces/{system_id}/select")
def select_workspace(
    system_id: str,
    request: Request,
    response: Response,
    context: PlatformContext = Depends(require_platform),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    system = _owned_workspace(db, context.account_id, system_id)
    platform = _account(db, context.account_id)
    owner = db.scalar(
        select(UserAccount)
        .where(
            UserAccount.system_id == system.id,
            UserAccount.platform_account_id == platform.id,
            UserAccount.is_owner.is_(True),
        )
        .execution_options(bypass_recruitment_scope=True)
    )
    if not owner:
        raise HTTPException(status_code=409, detail="This workspace is missing its owner account.")
    owner.username = platform.username
    owner.password_hash = platform.password_hash
    owner.active = True
    owner.can_admin = True
    owner.can_results = True
    owner.can_evaluate = True
    workspace_session = create_user_session(db, response, owner)
    set_system_cookie(response, system.slug)
    db.commit()
    return {
        "id": system.id,
        "name": system.name,
        "slug": system.slug,
        "workspaceCsrfToken": workspace_session.csrf_token,
    }
