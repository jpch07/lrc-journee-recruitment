from __future__ import annotations

from contextvars import ContextVar, Token


_current_system_id: ContextVar[str | None] = ContextVar("assessment_system_id", default=None)


def current_system_id() -> str | None:
    return _current_system_id.get()


def select_system(system_id: str | None) -> Token:
    return _current_system_id.set(system_id)


def reset_system(token: Token) -> None:
    _current_system_id.reset(token)
