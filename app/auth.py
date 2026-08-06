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
from .models import AdminSession, EvaluatorSession, IdempotencyRecord


ADMIN_COOKIE = "lrc_journee_admin"
EVALUATOR_COOKIE = "lrc_journee_evaluator"
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


def require_admin(request: Request, db: Session = Depends(get_db)) -> AdminContext:
    token = request.cookies.get(ADMIN_COOKIE, "")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required.")
    session = db.get(AdminSession, _token_hash(token))
    if not session or _aware(session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin session expired.")
    return AdminContext(session.actor_name, session.csrf_token, session.token_hash)


def require_evaluator(request: Request, db: Session = Depends(get_db)) -> EvaluatorContext:
    token = request.cookies.get(EVALUATOR_COOKIE, "")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Evaluator session required.")
    session = db.get(EvaluatorSession, _token_hash(token))
    if not session or _aware(session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Evaluator session expired.")
    return EvaluatorContext(session.journey_id, session.evaluator_id, session.csrf_token, session.token_hash)


def require_csrf(request: Request, expected: str) -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


def logout_admin(db: Session, response: Response, context: AdminContext) -> None:
    db.execute(delete(AdminSession).where(AdminSession.token_hash == context.token_hash))
    response.delete_cookie(ADMIN_COOKIE, path="/")


def logout_evaluator(db: Session, response: Response, context: EvaluatorContext) -> None:
    db.execute(delete(EvaluatorSession).where(EvaluatorSession.token_hash == context.token_hash))
    response.delete_cookie(EVALUATOR_COOKIE, path="/")


def clear_expired_sessions(db: Session) -> None:
    now = datetime.now(timezone.utc)
    db.execute(delete(AdminSession).where(AdminSession.expires_at < now))
    db.execute(delete(EvaluatorSession).where(EvaluatorSession.expires_at < now))
    db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at < now - timedelta(days=7)))
