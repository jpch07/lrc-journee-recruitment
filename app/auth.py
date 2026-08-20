from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import (
    AdminSession,
    Evaluator,
    EvaluatorDirectory,
    EvaluatorSession,
    IdempotencyRecord,
    Journey,
    JourneyPermission,
    RecruitAttendanceSession,
    PlatformAccount,
    PlatformSession,
    UserAccount,
    UserSession,
)


ADMIN_COOKIE = "lrc_journee_admin"
EVALUATOR_COOKIE = "lrc_journee_evaluator"
RECRUIT_ATTENDANCE_COOKIE = "lrc_journee_recruit_attendance"
USER_COOKIE = "lrc_journee_user"
SYSTEM_COOKIE = "assessment_recruitment"
PLATFORM_COOKIE = "assessment_platform"
_password_hasher = PasswordHasher()
_login_attempts: dict[str, list[float]] = {}


def _token_hash(token: str) -> str:
    return hashlib.sha256((settings.session_secret + token).encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def verify_admin_password(password: str) -> bool:
    if settings.admin_password_hash:
        try:
            return _password_hasher.verify(settings.admin_password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
    return secrets.compare_digest(password, settings.admin_password)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def ensure_owner_account(db: Session) -> UserAccount:
    from .assessment_runtime import default_assessor_category, assessor_category_keys
    owner_category = "dossard" if "dossard" in assessor_category_keys() else default_assessor_category()
    marita = db.scalar(select(EvaluatorDirectory).where(EvaluatorDirectory.name.ilike("Marita")))
    if marita:
        marita.active = False
    owner_directory = db.scalar(select(EvaluatorDirectory).where(EvaluatorDirectory.name.ilike("JP Chaaya")))
    account = db.scalar(select(UserAccount).where(UserAccount.username.ilike("JP Chaaya")))
    if account:
        account.is_owner = True
        account.can_admin = True
        account.can_results = True
        account.can_evaluate = True
        account.active = True
        account.evaluator_role = owner_category
        if owner_directory and not account.directory_id:
            account.directory_id = owner_directory.id
        return account
    password_hash = settings.admin_password_hash or hash_password(settings.admin_password)
    account = UserAccount(
        username="JP Chaaya",
        password_hash=password_hash,
        directory_id=owner_directory.id if owner_directory else None,
        evaluator_role=owner_category,
        is_owner=True,
        can_admin=True,
        can_results=True,
        can_evaluate=True,
        active=True,
        must_change_password=False,
    )
    db.add(account)
    return account


def ensure_platform_owner(db: Session, account: UserAccount) -> PlatformAccount:
    platform = db.scalar(
        select(PlatformAccount)
        .where(PlatformAccount.username.ilike(account.username))
        .execution_options(bypass_recruitment_scope=True)
    )
    if not platform:
        platform = PlatformAccount(username=account.username, password_hash=account.password_hash, active=True)
        db.add(platform)
        db.flush()
    account.platform_account_id = platform.id
    from .models import AssessmentSystem
    system = db.scalar(
        select(AssessmentSystem)
        .where(AssessmentSystem.id == account.system_id)
        .execution_options(bypass_recruitment_scope=True)
    )
    if system and not system.owner_platform_account_id:
        system.owner_platform_account_id = platform.id
    return platform


def enforce_login_rate_limit(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = [stamp for stamp in _login_attempts.get(key, []) if now - stamp < 300]
    if len(attempts) >= 10:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    attempts.append(now)
    _login_attempts[key] = attempts


def clear_login_attempts(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    _login_attempts.pop(key, None)


def _set_cookie(response: Response, name: str, token: str) -> None:
    response.set_cookie(
        name,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def set_system_cookie(response: Response, slug: str) -> None:
    response.set_cookie(
        SYSTEM_COOKIE,
        slug,
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def create_admin_session(db: Session, response: Response, actor_name: str) -> AdminSession:
    token = secrets.token_urlsafe(40)
    session = AdminSession(
        token_hash=_token_hash(token),
        actor_name=actor_name,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_hours),
    )
    db.add(session)
    _set_cookie(response, ADMIN_COOKIE, token)
    return session


def create_user_session(db: Session, response: Response, account: UserAccount) -> UserSession:
    token = secrets.token_urlsafe(40)
    session = UserSession(
        token_hash=_token_hash(token),
        account_id=account.id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_hours),
    )
    db.add(session)
    _set_cookie(response, USER_COOKIE, token)
    return session


def create_platform_session(db: Session, response: Response, account: PlatformAccount) -> PlatformSession:
    token = secrets.token_urlsafe(40)
    session = PlatformSession(
        token_hash=_token_hash(token),
        account_id=account.id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_hours),
    )
    db.add(session)
    _set_cookie(response, PLATFORM_COOKIE, token)
    return session


def create_evaluator_session(
    db: Session,
    response: Response,
    journey_id: str,
    evaluator_id: str,
) -> EvaluatorSession:
    token = secrets.token_urlsafe(40)
    session = EvaluatorSession(
        token_hash=_token_hash(token),
        journey_id=journey_id,
        evaluator_id=evaluator_id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_hours),
    )
    db.add(session)
    _set_cookie(response, EVALUATOR_COOKIE, token)
    return session


def create_recruit_attendance_session(
    db: Session,
    response: Response,
    journey_id: str,
    actor_name: str,
) -> RecruitAttendanceSession:
    token = secrets.token_urlsafe(40)
    session = RecruitAttendanceSession(
        token_hash=_token_hash(token),
        journey_id=journey_id,
        actor_name=actor_name,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_hours),
    )
    db.add(session)
    _set_cookie(response, RECRUIT_ATTENDANCE_COOKIE, token)
    return session


@dataclass(frozen=True)
class AdminContext:
    actor_name: str
    csrf_token: str
    token_hash: str


@dataclass(frozen=True)
class EvaluatorContext:
    journey_id: str
    evaluator_id: str
    csrf_token: str
    token_hash: str


@dataclass(frozen=True)
class RecruitAttendanceContext:
    journey_id: str
    actor_name: str
    csrf_token: str
    token_hash: str


@dataclass(frozen=True)
class UserContext:
    account_id: str
    system_id: str
    username: str
    csrf_token: str
    token_hash: str
    is_owner: bool
    can_admin: bool
    can_results: bool
    can_evaluate: bool
    directory_id: str | None
    evaluator_role: str


@dataclass(frozen=True)
class PlatformContext:
    account_id: str
    username: str
    csrf_token: str
    token_hash: str


def _user_context(request: Request, db: Session) -> UserContext | None:
    token = request.cookies.get(USER_COOKIE, "")
    if not token:
        return None
    session = db.get(UserSession, _token_hash(token))
    if not session or _aware(session.expires_at) <= datetime.now(timezone.utc):
        return None
    account = db.get(UserAccount, session.account_id)
    if not account or not account.active:
        return None
    return UserContext(
        account.id,
        account.system_id,
        account.username,
        session.csrf_token,
        session.token_hash,
        account.is_owner,
        account.can_admin,
        account.can_results,
        account.can_evaluate,
        account.directory_id,
        account.evaluator_role,
    )


def require_user(request: Request, db: Session = Depends(get_db)) -> UserContext:
    context = _user_context(request, db)
    if not context:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.")
    return context


def require_platform(request: Request, db: Session = Depends(get_db)) -> PlatformContext:
    token = request.cookies.get(PLATFORM_COOKIE, "")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.")
    session = db.get(PlatformSession, _token_hash(token))
    if not session or _aware(session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired.")
    account = db.get(PlatformAccount, session.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is no longer active.")
    return PlatformContext(account.id, account.username, session.csrf_token, session.token_hash)


def require_owner(request: Request, db: Session = Depends(get_db)) -> UserContext:
    context = require_user(request, db)
    if not context.is_owner:
        raise HTTPException(status_code=403, detail="Owner access is required.")
    return context


def require_results(request: Request, db: Session = Depends(get_db)) -> UserContext:
    context = require_user(request, db)
    if not (context.is_owner or context.can_admin or context.can_results):
        raise HTTPException(status_code=403, detail="Results access is not permitted.")
    return context


def require_admin(request: Request, db: Session = Depends(get_db)) -> AdminContext:
    user = _user_context(request, db)
    if user:
        if not (user.is_owner or user.can_admin):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administration access is not permitted.")
        return AdminContext(user.username, user.csrf_token, user.token_hash)
    if settings.is_production:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Named admin login required.")
    token = request.cookies.get(ADMIN_COOKIE, "")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required.")
    session = db.get(AdminSession, _token_hash(token))
    if not session or _aware(session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin session expired.")
    return AdminContext(session.actor_name, session.csrf_token, session.token_hash)


def require_evaluator(request: Request, db: Session = Depends(get_db)) -> EvaluatorContext:
    user = _user_context(request, db)
    if user:
        if not (user.is_owner or user.can_evaluate):
            raise HTTPException(status_code=403, detail="Evaluator access is not permitted for this account.")
        journey = db.scalar(select(Journey).where(Journey.status == "active"))
        if not journey:
            raise HTTPException(status_code=404, detail="No Journee is currently active.")
        evaluator = None
        if user.directory_id:
            evaluator = db.scalar(
                select(Evaluator).where(
                    Evaluator.journey_id == journey.id,
                    Evaluator.directory_id == user.directory_id,
                    Evaluator.active.is_(True),
                    Evaluator.present.is_(True),
                )
            )
        if not evaluator:
            evaluator = db.scalar(
                select(Evaluator).where(
                    Evaluator.journey_id == journey.id,
                    Evaluator.name.ilike(user.username),
                    Evaluator.active.is_(True),
                    Evaluator.present.is_(True),
                )
            )
        if not evaluator:
            raise HTTPException(status_code=403, detail="Your account is not marked present for the active Journee.")
        return EvaluatorContext(journey.id, evaluator.id, user.csrf_token, user.token_hash)
    if settings.is_production:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Named evaluator login required.")
    token = request.cookies.get(EVALUATOR_COOKIE, "")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Evaluator session required.")
    session = db.get(EvaluatorSession, _token_hash(token))
    if not session or _aware(session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Evaluator session expired.")
    return EvaluatorContext(session.journey_id, session.evaluator_id, session.csrf_token, session.token_hash)


def require_recruit_attendance(
    request: Request,
    db: Session = Depends(get_db),
) -> RecruitAttendanceContext:
    user = _user_context(request, db)
    if user:
        journey_id = str(request.path_params.get("journey_id") or request.query_params.get("journey_id") or "")
        if not journey_id:
            journey_id = request.cookies.get("lrc_attendance_journey", "")
        permission = db.scalar(
            select(JourneyPermission).where(
                JourneyPermission.account_id == user.account_id,
                JourneyPermission.journey_id == journey_id,
                JourneyPermission.can_attendance.is_(True),
            )
        ) if journey_id else None
        if not (user.is_owner or user.can_admin or permission):
            raise HTTPException(status_code=403, detail="Recruit attendance access is not permitted for this Journee.")
        if not journey_id:
            raise HTTPException(status_code=400, detail="A Journee is required for attendance access.")
        return RecruitAttendanceContext(journey_id, user.username, user.csrf_token, user.token_hash)
    if settings.is_production:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Named attendance login required.")
    token = request.cookies.get(RECRUIT_ATTENDANCE_COOKIE, "")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Recruit attendance session required.")
    session = db.get(RecruitAttendanceSession, _token_hash(token))
    if not session or _aware(session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Recruit attendance session expired.")
    return RecruitAttendanceContext(
        session.journey_id,
        session.actor_name,
        session.csrf_token,
        session.token_hash,
    )


def require_csrf(request: Request, expected: str) -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


def logout_admin(db: Session, response: Response, context: AdminContext) -> None:
    if db.get(UserSession, context.token_hash):
        db.execute(delete(UserSession).where(UserSession.token_hash == context.token_hash))
        response.delete_cookie(USER_COOKIE, path="/")
        return
    db.execute(delete(AdminSession).where(AdminSession.token_hash == context.token_hash))
    response.delete_cookie(ADMIN_COOKIE, path="/")


def logout_evaluator(db: Session, response: Response, context: EvaluatorContext) -> None:
    if db.get(UserSession, context.token_hash):
        db.execute(delete(UserSession).where(UserSession.token_hash == context.token_hash))
        response.delete_cookie(USER_COOKIE, path="/")
        return
    db.execute(delete(EvaluatorSession).where(EvaluatorSession.token_hash == context.token_hash))
    response.delete_cookie(EVALUATOR_COOKIE, path="/")


def logout_recruit_attendance(
    db: Session,
    response: Response,
    context: RecruitAttendanceContext,
) -> None:
    if db.get(UserSession, context.token_hash):
        db.execute(delete(UserSession).where(UserSession.token_hash == context.token_hash))
        response.delete_cookie(USER_COOKIE, path="/")
        return
    db.execute(
        delete(RecruitAttendanceSession).where(
            RecruitAttendanceSession.token_hash == context.token_hash
        )
    )
    response.delete_cookie(RECRUIT_ATTENDANCE_COOKIE, path="/")


def clear_expired_sessions(db: Session) -> None:
    now = datetime.now(timezone.utc)
    db.execute(delete(AdminSession).where(AdminSession.expires_at < now))
    db.execute(delete(EvaluatorSession).where(EvaluatorSession.expires_at < now))
    db.execute(delete(RecruitAttendanceSession).where(RecruitAttendanceSession.expires_at < now))
    db.execute(delete(UserSession).where(UserSession.expires_at < now))
    db.execute(delete(PlatformSession).where(PlatformSession.expires_at < now))
    db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at < now - timedelta(days=7)))
