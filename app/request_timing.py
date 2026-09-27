"""Temporary numeric-only request diagnostics. No queries or data writes are added.

This diagnostic branch automatically stops collecting at 2026-09-28 00:00 UTC.
Remove the branch deployment after diagnosis. Timings overlap; do not add them.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import json
import sys
import threading
from time import perf_counter
from typing import Callable
from uuid import uuid4

from sqlalchemy import event


CAPTURE_UNTIL = datetime(2026, 9, 28, tzinfo=timezone.utc)
PHASES = ("workspace", "runtime", "cookie", "ready", "acquire", "ping", "revision")
_CURRENT: ContextVar[Trace | None] = ContextVar("lrc_request_timing", default=None)
_PHASE: ContextVar[str] = ContextVar("lrc_timing_phase", default="handler")
_ATTR = "_lrc_diagnostic_timing_v1"


@dataclass
class Trace:
    category: str
    started: float = field(default_factory=perf_counter)
    identifier: str = field(default_factory=lambda: uuid4().hex[:12])
    durations: dict[str, float] = field(default_factory=dict)
    sql_by_phase: dict[str, float] = field(default_factory=dict)
    sql_count: int = 0
    sql_errors: int = 0
    sql_ms: float = 0.0
    sql_max_ms: float = 0.0
    active: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_phase(self, name: str, milliseconds: float) -> None:
        with self.lock:
            if self.active:
                self.durations[name] = self.durations.get(name, 0.0) + milliseconds

    def add_sql(self, name: str, milliseconds: float, failed: bool) -> None:
        with self.lock:
            if not self.active:
                return
            self.sql_count += 1
            self.sql_errors += int(failed)
            self.sql_ms += milliseconds
            self.sql_max_ms = max(self.sql_max_ms, milliseconds)
            self.sql_by_phase[name] = self.sql_by_phase.get(name, 0.0) + milliseconds

    def finish(self, status: int) -> dict:
        with self.lock:
            self.active = False
            return {
                "diagnostic": "v1",
                "id": self.identifier,
                "category": self.category,
                "status": int(status),
                "app_ms": round((perf_counter() - self.started) * 1000, 3),
                "sql_count": self.sql_count,
                "sql_errors": self.sql_errors,
                "sql_ms": round(self.sql_ms, 3),
                "sql_max_ms": round(self.sql_max_ms, 3),
                "phases_ms": {k: round(v, 3) for k, v in self.durations.items()},
                "sql_by_phase_ms": {k: round(v, 3) for k, v in self.sql_by_phase.items()},
            }


@contextmanager
def phase(name: str):
    """Time an existing operation; never change its result or exceptions."""
    trace = _CURRENT.get()
    if trace is None or name not in PHASES:
        yield
        return
    token = _PHASE.set(name)
    started = perf_counter()
    try:
        yield
    finally:
        elapsed = (perf_counter() - started) * 1000
        _PHASE.reset(token)
        try:
            trace.add_phase(name, elapsed)
        except Exception:
            pass  # Diagnostic bookkeeping must not fail an application request.


def timed(name: str):
    """Apply phase timing to a synchronous helper without changing its signature."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with phase(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    # Do not store or log statement text, parameters, results, URLs, or credentials.
    trace = _CURRENT.get()
    if trace is not None and trace.active and context is not None:
        try:
            setattr(context, _ATTR, (trace, _PHASE.get(), perf_counter()))
        except Exception:
            pass


def _finish_statement(context, failed: bool) -> None:
    if context is None:
        return
    try:
        timing = getattr(context, _ATTR, None)
        if timing is None:
            return
        delattr(context, _ATTR)
        trace, name, started = timing
        trace.add_sql(name, (perf_counter() - started) * 1000, failed)
    except Exception:
        pass


def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    _finish_statement(context, False)


def _handle_error(exception_context):
    # No return value: SQLAlchemy's original exception handling is unchanged.
    _finish_statement(exception_context.execution_context, True)


def install_engine_timing(engine) -> None:
    for name, callback in (
        ("before_cursor_execute", _before_cursor_execute),
        ("after_cursor_execute", _after_cursor_execute),
        ("handle_error", _handle_error),
    ):
        if not event.contains(engine, name, callback):
            event.listen(engine, name, callback)


def _category(path: str) -> str | None:
    if path == "/health/live":
        return "health_live"
    if path == "/health/ready":
        return "health_ready"
    if path.startswith("/api/admin/"):
        if path.endswith("/dashboard"):
            return "admin_dashboard"
        return "admin_api"
    if path.startswith("/api/view/"):
        return "management_api"
    return None


def _server_timing(record: dict) -> str:
    parts = [
        f'lrc_app;dur={record["app_ms"]:.3f}',
        f'lrc_sql;dur={record["sql_ms"]:.3f}',
        f'lrc_sql_max;dur={record["sql_max_ms"]:.3f}',
        f'lrc_queries;desc="{record["sql_count"]}"',
        f'lrc_sql_errors;desc="{record["sql_errors"]}"',
    ]
    for name in PHASES:
        if name in record["phases_ms"]:
            parts.append(f'lrc_{name};dur={record["phases_ms"][name]:.3f}')
    return ", ".join(parts)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RequestTimingMiddleware:
    """Pure ASGI wrapper: no body reads, buffering, auth changes, or new requests."""

    def __init__(self, app, expires_at=CAPTURE_UNTIL, clock: Callable = _now):
        self.app = app
        self.expires_at = expires_at
        self.clock = clock
        # Bound the number of log lines per process; headers still provide timing.
        self._budgets = {"health": 20, "api": 150}
        self._budget_lock = threading.Lock()

    def _log(self, record: dict) -> None:
        key = "health" if record["category"].startswith("health_") else "api"
        with self._budget_lock:
            if self._budgets[key] <= 0:
                return
            self._budgets[key] -= 1
        # Alembic's existing fileConfig() disables pre-existing loggers during
        # startup. Emit ONLY our bounded numeric diagnostic directly to stderr
        # rather than changing migration or global application logging settings.
        # timed_send already catches sink errors so responses stay unaffected.
        line = "LRC_TIMING_V1 " + json.dumps(record, separators=(",", ":")) + "\n"
        sys.stderr.write(line)
        sys.stderr.flush()

    async def __call__(self, scope, receive, send):
        category = _category(scope.get("path", "")) if scope["type"] == "http" else None
        if category is None or self.clock() >= self.expires_at:
            await self.app(scope, receive, send)
            return
        trace = Trace(category)
        token = _CURRENT.set(trace)
        phase_token = _PHASE.set("handler")
        emitted = False

        async def timed_send(message):
            nonlocal emitted
            if message["type"] == "http.response.start" and not emitted:
                emitted = True
                try:
                    record = trace.finish(message["status"])
                    headers = list(message.get("headers", []))
                    previous = [v.decode("latin-1") for k, v in headers if k.lower() == b"server-timing"]
                    headers = [(k, v) for k, v in headers if k.lower() != b"server-timing"]
                    timing = ", ".join(previous + [_server_timing(record)])
                    headers.append((b"server-timing", timing.encode("latin-1")))
                    headers.append((b"x-lrc-timing-id", trace.identifier.encode("ascii")))
                    message = {**message, "headers": headers}
                    self._log(record)
                except Exception:
                    trace.active = False
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            if not emitted:
                try:
                    self._log(trace.finish(500))
                except Exception:
                    pass
            _PHASE.reset(phase_token)
            _CURRENT.reset(token)


def install_request_timing(app, engine) -> None:
    """Call once after the existing middleware is registered, before startup."""
    install_engine_timing(engine)
    app.add_middleware(RequestTimingMiddleware)
