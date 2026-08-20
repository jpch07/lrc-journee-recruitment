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
    set_system_cookie,
    verify_password,
)
from .db import get_db
from .config import settings
from .models import AssessmentSystem, AuditEvent, Evaluator, EvaluatorDirectory, Journey, JourneyPermission, PlatformAccount, UserAccount, UserSession, utcnow
from .schemas import (
    AccountCreateRequest,
    AccountLoginRequest,
    AccountUpdateRequest,
    RecruitmentSelectionRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
)
from .utils import audit
from .assessment_runtime import active_assessment_definition, assessor_category_keys, default_assessor_category
from .services import populate_journey_evaluators_from_directory
from .tenant import current_system_id, select_system
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
    include_contact: bool = False,
    directory: EvaluatorDirectory | None = None,
) -> dict:
    if directory is None and account.directory_id:
        directory = db.get(EvaluatorDirectory, account.directory_id)
    system = db.get(AssessmentSystem, account.system_id)
    payload = {
        "id": account.id,
        "username": account.username,
        "fullName": directory.full_name if directory and directory.full_name else "",
        "evaluatorRole": account.evaluator_role,
        "isOwner": account.is_owner,
        "canAdmin": account.can_admin,
        "canResults": account.can_results,
        "canEvaluate": account.can_evaluate,
        "active": account.active,
        "mustChangePassword": account.must_change_password,
        "version": account.version,
        "attendanceJourneyIds": _permission_ids(db, account.id) if include_permissions else [],
        "testToolsEnabled": settings.test_tools_enabled,
        "recruitment": {"id": system.id, "name": system.name, "slug": system.slug} if system else None,
    }
    if include_managed_password:
        payload["managedPassword"] = account.managed_password
    if include_contact:
        payload["phoneNumber"] = directory.phone_number if directory and directory.phone_number else ""
    return payload


def simple_managed_password(username: str) -> str:
    normalized = unicodedata.normalize("NFKD", username).casefold()
    compact = "".join(character for character in normalized if character.isalnum()) or "user"
    return f"{compact}{secrets.randbelow(900) + 100}"


def _current_account(db: Session, context: UserContext) -> UserAccount:
    account = db.get(UserAccount, context.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=401, detail="Account is no longer active.")
    return account


def _sync_platform_owner_password(db: Session, account: UserAccount) -> None:
    """Keep the owner's one global password identical in every owned workspace."""
    if not account.platform_account_id:
        return
    platform = db.scalar(
        select(PlatformAccount)
        .where(PlatformAccount.id == account.platform_account_id)
        .execution_options(bypass_recruitment_scope=True)
    )
    if not platform:
        return
    platform.password_hash = account.password_hash
    platform.version += 1
    linked_owners = list(db.scalars(
        select(UserAccount)
        .where(
            UserAccount.platform_account_id == platform.id,
            UserAccount.is_owner.is_(True),
        )
        .execution_options(bypass_recruitment_scope=True)
    ))
    for linked_owner in linked_owners:
        linked_owner.password_hash = account.password_hash


def _system_by_slug(db: Session, slug: str) -> AssessmentSystem | None:
    return db.scalar(
        select(AssessmentSystem)
        .where(func.lower(AssessmentSystem.slug) == slug.strip().casefold())
        .execution_options(bypass_recruitment_scope=True)
    )


@router.get("/recruitment")
def selected_recruitment(db: Session = Depends(get_db)):
    system_id = current_system_id()
    if not system_id:
        return {"selected": False}
    system = db.get(AssessmentSystem, system_id)
    return {"selected": True, "id": system.id, "name": system.name, "slug": system.slug} if system else {"selected": False}


@router.post("/select-recruitment")
def select_recruitment(payload: RecruitmentSelectionRequest, response: Response, db: Session = Depends(get_db)):
    system = _system_by_slug(db, payload.recruitment)
    if not system or system.status != "active":
        raise HTTPException(status_code=404, detail="Recruitment not found.")
    set_system_cookie(response, system.slug)
    return {"id": system.id, "name": system.name, "slug": system.slug}


@router.get("/usernames")
def usernames(recruitment: str | None = None, db: Session = Depends(get_db)):
    system_id = current_system_id()
    if not system_id and recruitment:
        system = _system_by_slug(db, recruitment)
        system_id = system.id if system else None
    if not system_id:
        return []
    return [
        {
            "username": account.username,
            "fullName": directory.full_name if directory and directory.full_name else "",
            "role": account.evaluator_role,
        }
        for account, directory in db.execute(
            select(UserAccount, EvaluatorDirectory)
            .outerjoin(EvaluatorDirectory, UserAccount.directory_id == EvaluatorDirectory.id)
            .where(UserAccount.system_id == system_id, UserAccount.active.is_(True))
            .order_by(func.lower(UserAccount.username))
            .execution_options(bypass_recruitment_scope=True)
        )
    ]


@router.post("/login")
def login(payload: AccountLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_login_rate_limit(request)
    username = " ".join(payload.username.split())
    system = _system_by_slug(db, payload.recruitment) if payload.recruitment else None
    selected_id = system.id if system else current_system_id()
    if selected_id:
        account = db.scalar(
            select(UserAccount).where(
                UserAccount.system_id == selected_id,
                func.lower(UserAccount.username) == username.lower(),
            ).execution_options(bypass_recruitment_scope=True)
        )
    else:
        matches = list(db.scalars(
            select(UserAccount).where(func.lower(UserAccount.username) == username.lower())
            .execution_options(bypass_recruitment_scope=True)
        ))
        account = matches[0] if len(matches) == 1 else None
    if not account or not account.active or not verify_password(account.password_hash, payload.password):
        raise HTTPException(status_code=403, detail="Invalid username or password.")
    select_system(account.system_id)
    clear_login_attempts(request)
    clear_expired_sessions(db)
    session = create_user_session(db, response, account)
    system = db.get(AssessmentSystem, account.system_id) if current_system_id() == account.system_id else db.scalar(
        select(AssessmentSystem).where(AssessmentSystem.id == account.system_id).execution_options(bypass_recruitment_scope=True)
    )
    if system:
        set_system_cookie(response, system.slug)
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
    _sync_platform_owner_password(db, account)
    db.execute(delete(UserSession).where(UserSession.account_id == account.id, UserSession.token_hash != context.token_hash))
    audit(db, journey_id=None, actor_type="account", actor_name=account.username,
          action="account.password_changed", entity_type="user_account", entity_id=account.id)
    _commit(db)
    return {"ok": True, "version": account.version}


def create_account_record(
    db: Session,
    username: str,
    password: str,
    evaluator_role: str,
    full_name: str | None = None,
    phone_number: str | None = None,
) -> UserAccount:
    evaluator_role = evaluator_role or default_assessor_category()
    if evaluator_role not in assessor_category_keys():
        raise HTTPException(status_code=422, detail="Select a configured assessor category.")
    clean = " ".join(username.split())
    clean_full_name = " ".join((full_name or "").split()) or None
    clean_phone = (phone_number or "").strip() or None
    existing = db.scalar(select(UserAccount).where(func.lower(UserAccount.username) == clean.lower()))
    if existing:
        raise HTTPException(status_code=409, detail="This username already has an account.")
    directory = db.scalar(select(EvaluatorDirectory).where(func.lower(EvaluatorDirectory.name) == clean.lower()))
    if not directory:
        directory = EvaluatorDirectory(
            name=clean,
            default_role=evaluator_role,
            full_name=clean_full_name,
            phone_number=clean_phone,
            active=True,
        )
        db.add(directory)
        db.flush()
    else:
        directory.active = True
        directory.default_role = evaluator_role
        if full_name is not None:
            directory.full_name = clean_full_name
        if phone_number is not None:
            directory.phone_number = clean_phone
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
    # The assessor directory is workspace-wide. Existing Sessions keep their
    # own attendance state, but every newly created account must immediately be
    # available there as an absent assessor just like accounts created from an
    # attendance page.
    for journey_id in list(db.scalars(select(Journey.id))):
        populate_journey_evaluators_from_directory(db, journey_id)
    db.flush()
    return account


@router.get("/accounts")
def accounts(
    context: UserContext = Depends(require_owner),
    db: Session = Depends(get_db),
):
    del context
    return [
        account_payload(
            db,
            account,
            include_managed_password=True,
            include_contact=True,
            directory=directory,
        )
        for account, directory in db.execute(
            select(UserAccount, EvaluatorDirectory)
            .outerjoin(EvaluatorDirectory, UserAccount.directory_id == EvaluatorDirectory.id)
            .order_by(UserAccount.is_owner.desc(), func.lower(UserAccount.username))
        )
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
    account = create_account_record(
        db,
        payload.username,
        payload.password,
        payload.evaluator_role,
        payload.full_name,
        payload.phone_number,
    )
    profile = next((item for item in active_assessment_definition().accessProfiles
                    if item.enabled and item.key == payload.access_profile), None)
    if not profile or profile.key == "owner":
        raise HTTPException(status_code=422, detail="Select an enabled non-owner access profile.")
    account.can_admin = "admin" in profile.capabilities
    account.can_results = "results" in profile.capabilities
    account.can_evaluate = "evaluate" in profile.capabilities
    if "attendance" in profile.capabilities and payload.attendance_journey_id:
        if not db.get(Journey, payload.attendance_journey_id):
            raise HTTPException(status_code=422, detail="Attendance Journey not found.")
        db.add(JourneyPermission(account_id=account.id, journey_id=payload.attendance_journey_id, can_attendance=True))
    audit(db, journey_id=None, actor_type="account", actor_name=context.username,
          action="account.created", entity_type="user_account", entity_id=account.id,
          after={
              "username": account.username,
              "evaluatorRole": account.evaluator_role,
              "fullName": " ".join((payload.full_name or "").split()),
              "phoneNumber": (payload.phone_number or "").strip(),
          })
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
    before = account_payload(db, account, include_contact=True)
    directory = db.get(EvaluatorDirectory, account.directory_id) if account.directory_id else None
    if "username" in payload.model_fields_set and payload.username is not None:
        clean_username = " ".join(payload.username.split())
        if account.is_owner and clean_username.casefold() != account.username.casefold():
            raise HTTPException(status_code=409, detail="The owner username cannot be changed.")
        duplicate_account = db.scalar(
            select(UserAccount).where(
                func.lower(UserAccount.username) == clean_username.lower(),
                UserAccount.id != account.id,
            )
        )
        if duplicate_account:
            raise HTTPException(status_code=409, detail="This username is already in use.")
        if directory:
            duplicate_directory = db.scalar(
                select(EvaluatorDirectory).where(
                    func.lower(EvaluatorDirectory.name) == clean_username.lower(),
                    EvaluatorDirectory.id != directory.id,
                )
            )
            if duplicate_directory:
                raise HTTPException(status_code=409, detail="This evaluator username is already in the directory.")
            directory.name = clean_username
        account.username = clean_username
    if not directory and account.directory_id is None:
        directory = EvaluatorDirectory(
            name=account.username,
            default_role=account.evaluator_role,
            active=True,
        )
        db.add(directory)
        db.flush()
        account.directory_id = directory.id
        for journey_id in list(db.scalars(select(Journey.id))):
            populate_journey_evaluators_from_directory(db, journey_id)
    if directory:
        if "full_name" in payload.model_fields_set:
            directory.full_name = " ".join((payload.full_name or "").split()) or None
        if "phone_number" in payload.model_fields_set:
            directory.phone_number = (payload.phone_number or "").strip() or None
    if payload.evaluator_role is not None and payload.evaluator_role not in assessor_category_keys():
        raise HTTPException(status_code=422, detail="Select a configured assessor category.")
    for field in ("can_admin", "can_results", "can_evaluate", "active", "evaluator_role"):
        value = getattr(payload, field)
        if value is not None:
            setattr(account, field, value)
    if account.is_owner:
        account.can_admin = True
        account.can_results = True
        account.can_evaluate = True
        account.active = True
        account.evaluator_role = "dossard" if "dossard" in assessor_category_keys() else default_assessor_category()
    if payload.evaluator_role is not None and directory:
        directory.default_role = account.evaluator_role
    if payload.attendance_journey_ids is not None:
        valid_ids = set(db.scalars(select(Journey.id).where(Journey.id.in_(payload.attendance_journey_ids)))) if payload.attendance_journey_ids else set()
        db.execute(delete(JourneyPermission).where(JourneyPermission.account_id == account.id))
        for journey_id in valid_ids:
            db.add(JourneyPermission(account_id=account.id, journey_id=journey_id, can_attendance=True))
    account.version += 1
    audit(db, journey_id=None, actor_type="owner", actor_name=context.username,
          action="account.updated", entity_type="user_account", entity_id=account.id,
          before=before, after=account_payload(db, account, include_contact=True))
    _commit(db)
    return account_payload(db, account, include_contact=True)


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: str,
    request: Request,
    context: UserContext = Depends(require_owner),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    account = db.get(UserAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if account.is_owner:
        raise HTTPException(status_code=409, detail="The owner account cannot be deleted.")
    if account.directory_id:
        active_evaluator = db.scalar(
            select(Evaluator.id)
            .join(Journey, Journey.id == Evaluator.journey_id)
            .where(
                Evaluator.directory_id == account.directory_id,
                Evaluator.active.is_(True),
                Evaluator.present.is_(True),
                Journey.status == "active",
            )
            .limit(1)
        )
        if active_evaluator:
            raise HTTPException(
                status_code=409,
                detail="This evaluator is present in the active Journee. Mark them absent before deleting the account.",
            )
    before = account_payload(db, account, include_contact=True)
    directory = db.get(EvaluatorDirectory, account.directory_id) if account.directory_id else None
    if directory:
        directory.active = False
    db.execute(delete(UserSession).where(UserSession.account_id == account.id))
    db.execute(delete(JourneyPermission).where(JourneyPermission.account_id == account.id))
    db.delete(account)
    audit(
        db,
        journey_id=None,
        actor_type="owner",
        actor_name=context.username,
        action="account.deleted",
        entity_type="user_account",
        entity_id=account.id,
        before=before,
        after={"deleted": True, "historicalJourneeDataPreserved": True},
    )
    _commit(db)
    return {"ok": True}


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
    _sync_platform_owner_password(db, account)
    db.execute(delete(UserSession).where(UserSession.account_id == account.id))
    audit(db, journey_id=None, actor_type="account", actor_name=context.username,
          action="account.password_reset", entity_type="user_account", entity_id=account.id,
          after={"username": account.username})
    _commit(db)
    return {"ok": True, "version": account.version}
