from __future__ import annotations

import secrets
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .auth import (
    UserContext,
    clear_expired_sessions,
    clear_login_attempts,
    create_user_session,
    enforce_login_rate_limit,
    hash_password,
    require_csrf,
    require_owner,
    require_user,
    verify_password,
)
from .db import get_db
from .config import settings
from .models import AuditEvent, EvaluatorDirectory, Journey, JourneyPermission, UserAccount, UserSession, utcnow
from .schemas import (
    AccountCreateRequest,
    AccountLoginRequest,
    AccountUpdateRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
)
from .utils import audit
from .utils import loads


router = APIRouter(prefix="/api/auth", tags=["authentication"])
ACCOUNT_GENERATION_BATCH_SIZE = 8


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _permission_ids(db: Session, account_id: str) -> list[str]:
    return list(
        db.scalars(
            select(JourneyPermission.journey_id).where(
                JourneyPermission.account_id == account_id,
                JourneyPermission.can_attendance.is_(True),
            )
        )
    )


def account_payload(
    db: Session,
    account: UserAccount,
    *,
    include_permissions: bool = True,
    include_managed_password: bool = False,
) -> dict:
    payload = {
        "id": account.id,
        "username": account.username,
        "evaluatorRole": account.evaluator_role,
        "isOwner": account.is_owner,
        "canAdmin": account.can_admin,
        "canResults": account.can_results,
        "active": account.active,
        "mustChangePassword": account.must_change_password,
        "version": account.version,
        "attendanceJourneyIds": _permission_ids(db, account.id) if include_permissions else [],
        "testToolsEnabled": settings.test_tools_enabled,
    }
    if include_managed_password:
        payload["managedPassword"] = account.managed_password
    return payload


def simple_managed_password(username: str) -> str:
    normalized = unicodedata.normalize("NFKD", username).casefold()
    compact = "".join(character for character in normalized if character.isalnum()) or "lrcuser"
    return f"{compact}{secrets.randbelow(900) + 100}"


def _current_account(db: Session, context: UserContext) -> UserAccount:
    account = db.get(UserAccount, context.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=401, detail="Account is no longer active.")
    return account


@router.get("/usernames")
def usernames(db: Session = Depends(get_db)):
    return [
        {"username": item.username, "role": item.evaluator_role}
        for item in db.scalars(
            select(UserAccount).where(UserAccount.active.is_(True)).order_by(func.lower(UserAccount.username))
        )
    ]


@router.post("/login")
def login(payload: AccountLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_login_rate_limit(request)
    username = " ".join(payload.username.split())
    account = db.scalar(select(UserAccount).where(func.lower(UserAccount.username) == username.lower()))
    if not account or not account.active or not verify_password(account.password_hash, payload.password):
        raise HTTPException(status_code=403, detail="Invalid username or password.")
    clear_login_attempts(request)
    clear_expired_sessions(db)
    session = create_user_session(db, response, account)
    audit(
        db,
        journey_id=None,
        actor_type="account",
        actor_name=account.username,
        action="account.login",
        entity_type="user_account",
        entity_id=account.id,
        after={"username": account.username},
    )
    _commit(db)
    return {**account_payload(db, account), "csrfToken": session.csrf_token}


@router.get("/session")
def session(context: UserContext = Depends(require_user), db: Session = Depends(get_db)):
    return {**account_payload(db, _current_account(db, context)), "csrfToken": context.csrf_token}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    context: UserContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    db.execute(delete(UserSession).where(UserSession.token_hash == context.token_hash))
    response.delete_cookie("lrc_journee_user", path="/")
    response.delete_cookie("lrc_attendance_journey", path="/")
    _commit(db)
    return {"ok": True}


@router.post("/change-password")
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    context: UserContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    account = _current_account(db, context)
    if not verify_password(account.password_hash, payload.current_password):
        raise HTTPException(status_code=403, detail="Current password is incorrect.")
    account.password_hash = hash_password(payload.new_password)
    account.managed_password = payload.new_password
    account.must_change_password = False
    account.version += 1
    db.execute(delete(UserSession).where(UserSession.account_id == account.id, UserSession.token_hash != context.token_hash))
    audit(db, journey_id=None, actor_type="account", actor_name=account.username,
          action="account.password_changed", entity_type="user_account", entity_id=account.id)
    _commit(db)
    return {"ok": True, "version": account.version}


def create_account_record(db: Session, username: str, password: str, evaluator_role: str) -> UserAccount:
    clean = " ".join(username.split())
    existing = db.scalar(select(UserAccount).where(func.lower(UserAccount.username) == clean.lower()))
    if existing:
        raise HTTPException(status_code=409, detail="This username already has an account.")
    directory = db.scalar(select(EvaluatorDirectory).where(func.lower(EvaluatorDirectory.name) == clean.lower()))
    if not directory:
        directory = EvaluatorDirectory(name=clean, default_role=evaluator_role, active=True)
        db.add(directory)
        db.flush()
    else:
        directory.active = True
        directory.default_role = evaluator_role
    account = UserAccount(
        username=clean,
        password_hash=hash_password(password),
        managed_password=password,
        directory_id=directory.id,
        evaluator_role=evaluator_role,
        must_change_password=True,
    )
    db.add(account)
    db.flush()
    return account


@router.get("/accounts")
def accounts(
    context: UserContext = Depends(require_owner),
    db: Session = Depends(get_db),
):
    del context
    return [
        account_payload(db, item, include_managed_password=True)
        for item in db.scalars(select(UserAccount).order_by(UserAccount.is_owner.desc(), func.lower(UserAccount.username)))
    ]


@router.get("/account-audit")
def account_audit(context: UserContext = Depends(require_owner), db: Session = Depends(get_db)):
    del context
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.journey_id.is_(None)).order_by(AuditEvent.created_at.desc()).limit(500)))
    return [{
        "id": item.id,
        "actorType": item.actor_type,
        "actorName": item.actor_name,
        "action": item.action,
        "entityType": item.entity_type,
        "entityId": item.entity_id,
        "before": loads(item.before_json, {}),
        "after": loads(item.after_json, {}),
        "reason": item.reason,
        "createdAt": item.created_at.isoformat(),
    } for item in events]


@router.post("/accounts")
def add_account(
    payload: AccountCreateRequest,
    request: Request,
    context: UserContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not (context.is_owner or context.can_admin):
        raise HTTPException(status_code=403, detail="Admin access is required to add accounts.")
    require_csrf(request, context.csrf_token)
    account = create_account_record(db, payload.username, payload.password, payload.evaluator_role)
    audit(db, journey_id=None, actor_type="account", actor_name=context.username,
          action="account.created", entity_type="user_account", entity_id=account.id,
          after={"username": account.username, "evaluatorRole": account.evaluator_role})
    _commit(db)
    return account_payload(db, account)


@router.post("/accounts/generate-missing")
def generate_missing_accounts(
    request: Request,
    context: UserContext = Depends(require_owner),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    existing_directory_ids = set(db.scalars(select(UserAccount.directory_id).where(UserAccount.directory_id.is_not(None))))
    existing_usernames = {
        name.casefold() for name in db.scalars(select(UserAccount.username))
    }
    missing_directories = [
        directory
        for directory in db.scalars(
            select(EvaluatorDirectory)
            .where(EvaluatorDirectory.active.is_(True), func.lower(EvaluatorDirectory.name) != "marita")
            .order_by(func.lower(EvaluatorDirectory.name))
        )
        if directory.id not in existing_directory_ids
        and directory.name.casefold() != "jp chaaya"
        and directory.name.casefold() not in existing_usernames
    ]
    accounts_without_managed_password = list(db.scalars(
        select(UserAccount)
        .where(
            UserAccount.active.is_(True),
            UserAccount.is_owner.is_(False),
            UserAccount.managed_password.is_(None),
            func.lower(UserAccount.username) != "marita",
        )
        .order_by(func.lower(UserAccount.username))
    ))
    pending: list[tuple[str, UserAccount | EvaluatorDirectory]] = [
        ("reset", account) for account in accounts_without_managed_password
    ] + [("create", directory) for directory in missing_directories]
    batch = pending[:ACCOUNT_GENERATION_BATCH_SIZE]
    credentials: list[dict] = []
    reset_count = 0
    for action, item in batch:
        password = simple_managed_password(item.username if action == "reset" else item.name)
        if action == "reset":
            account = item
            account.password_hash = hash_password(password)
            account.managed_password = password
            account.must_change_password = True
            account.version += 1
            db.execute(delete(UserSession).where(UserSession.account_id == account.id))
            reset_count += 1
        else:
            directory = item
            account = create_account_record(db, directory.name, password, directory.default_role)
        credentials.append({"username": account.username, "password": password, "role": account.evaluator_role})
    audit(db, journey_id=None, actor_type="owner", actor_name=context.username,
          action="accounts.initial_password_batch_generated", entity_type="user_account",
          after={
              "count": len(credentials),
              "created": len(batch) - reset_count,
              "reset": reset_count,
              "remaining": len(pending) - len(batch),
              "usernames": [item["username"] for item in credentials],
          })
    _commit(db)
    return {
        "created": credentials,
        "remaining": len(pending) - len(batch),
        "shownOnce": True,
    }


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: str,
    payload: AccountUpdateRequest,
    request: Request,
    context: UserContext = Depends(require_owner),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    account = db.get(UserAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if account.is_owner and account.id != context.account_id:
        raise HTTPException(status_code=409, detail="The owner account cannot be changed here.")
    if payload.base_version is not None and payload.base_version != account.version:
        raise HTTPException(status_code=409, detail="This account changed elsewhere. Reload before saving.")
    before = account_payload(db, account)
    for field in ("can_admin", "can_results", "active", "evaluator_role"):
        value = getattr(payload, field)
        if value is not None:
            setattr(account, field, value)
    if payload.evaluator_role is not None and account.directory_id:
        directory = db.get(EvaluatorDirectory, account.directory_id)
        if directory:
            directory.default_role = payload.evaluator_role
    if account.is_owner:
        account.can_admin = True
        account.can_results = True
        account.active = True
    if payload.attendance_journey_ids is not None:
        valid_ids = set(db.scalars(select(Journey.id).where(Journey.id.in_(payload.attendance_journey_ids)))) if payload.attendance_journey_ids else set()
        db.execute(delete(JourneyPermission).where(JourneyPermission.account_id == account.id))
        for journey_id in valid_ids:
            db.add(JourneyPermission(account_id=account.id, journey_id=journey_id, can_attendance=True))
    account.version += 1
    audit(db, journey_id=None, actor_type="owner", actor_name=context.username,
          action="account.permissions_updated", entity_type="user_account", entity_id=account.id,
          before=before, after=account_payload(db, account))
    _commit(db)
    return account_payload(db, account)


@router.post("/accounts/{account_id}/reset-password")
def reset_password(
    account_id: str,
    payload: PasswordResetRequest,
    request: Request,
    context: UserContext = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not (context.is_owner or context.can_admin):
        raise HTTPException(status_code=403, detail="Admin access is required.")
    require_csrf(request, context.csrf_token)
    account = db.get(UserAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if account.is_owner and not context.is_owner:
        raise HTTPException(status_code=403, detail="Only the owner can reset the owner password.")
    account.password_hash = hash_password(payload.new_password)
    account.managed_password = payload.new_password
    account.must_change_password = True
    account.version += 1
    db.execute(delete(UserSession).where(UserSession.account_id == account.id))
    audit(db, journey_id=None, actor_type="account", actor_name=context.username,
          action="account.password_reset", entity_type="user_account", entity_id=account.id,
          after={"username": account.username})
    _commit(db)
    return {"ok": True, "version": account.version}
