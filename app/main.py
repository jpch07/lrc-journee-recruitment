from __future__ import annotations

from contextlib import asynccontextmanager
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR, settings
from .auth import SYSTEM_COOKIE, USER_COOKIE, _token_hash, ensure_owner_account, ensure_platform_owner, set_system_cookie
from .db import SessionLocal, initialize_database
from .routes_admin import router as admin_router
from .routes_evaluator import router as evaluator_router
from .routes_attendance import router as attendance_router
from .routes_auth import router as auth_router
from .routes_viewer import router as viewer_router
from .routes_configurator import router as configurator_router
from .routes_platform import router as platform_router
from .rubric import validate_rubrics
from .assessment_service import ensure_assessment_system
from .assessment_runtime import active_assessment_definition, activate_assessment_definition, reset_assessment_definition
from .assessment_config import AssessmentSystemDefinition
from .models import AssessmentSystem, AssessmentSystemVersion, Journey, RecruitAttendanceAccess, UserAccount, UserSession
from .tenant import reset_system, select_system
from .utils import loads
from sqlalchemy import select


database_startup_error: str | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global database_startup_error
    settings.validate_production()
    validate_rubrics()
    try:
        initialize_database()
        database_startup_error = None
    except Exception as exc:  # Keep liveness and diagnostics available.
        database_startup_error = str(exc)
        yield
        return
    with SessionLocal() as db:
        system = db.scalar(select(AssessmentSystem).order_by(AssessmentSystem.created_at).limit(1).execution_options(bypass_recruitment_scope=True))
        if not system and not settings.is_production:
            system = ensure_assessment_system(db)
            db.flush()
        if system:
            system_token = select_system(system.id)
            try:
                record = db.scalar(select(AssessmentSystemVersion).where(
                    AssessmentSystemVersion.system_id == system.id,
                    AssessmentSystemVersion.version == system.published_version,
                ))
                runtime_token = activate_assessment_definition(
                    AssessmentSystemDefinition.model_validate(loads(record.definition_json, {}))
                ) if record else None
                owner_exists = db.scalar(select(UserAccount.id).where(UserAccount.is_owner.is_(True)))
                if owner_exists or not settings.is_production:
                    owner = ensure_owner_account(db)
                    db.flush()
                    ensure_platform_owner(db, owner)
                db.commit()
                reset_assessment_definition(runtime_token)
            finally:
                reset_system(system_token)
    yield


app = FastAPI(
    title="Assessment Workspace",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(admin_router)
app.include_router(evaluator_router)
app.include_router(attendance_router)
app.include_router(auth_router)
app.include_router(viewer_router)
app.include_router(configurator_router)
app.include_router(platform_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _request_system_id(request: Request) -> str | None:
    """Resolve the recruitment before dependencies and scoring code run."""
    with SessionLocal() as db:
        path = request.url.path
        workspace_match = re.match(r"^/([^/]+)(?:/|$)", path)
        if workspace_match and workspace_match.group(1) not in {
            "api", "static", "health", "admin", "configure", "evaluate",
            "view", "j", "recruit-attendance",
        }:
            workspace_id = db.scalar(
                select(AssessmentSystem.id)
                .where(
                    AssessmentSystem.slug == workspace_match.group(1),
                    AssessmentSystem.status == "active",
                )
                .execution_options(bypass_recruitment_scope=True)
            )
            if workspace_id:
                return workspace_id
        user_token = request.cookies.get(USER_COOKIE, "")
        if user_token:
            account_id = db.scalar(
                select(UserSession.account_id)
                .where(UserSession.token_hash == _token_hash(user_token))
                .execution_options(bypass_recruitment_scope=True)
            )
            if account_id:
                system_id = db.scalar(
                    select(UserAccount.system_id)
                    .where(UserAccount.id == account_id)
                    .execution_options(bypass_recruitment_scope=True)
                )
                if system_id:
                    return system_id
        slug = request.cookies.get(SYSTEM_COOKIE, "") or request.query_params.get("recruitment", "")
        if slug:
            system_id = db.scalar(
                select(AssessmentSystem.id)
                .where(AssessmentSystem.slug == slug)
                .execution_options(bypass_recruitment_scope=True)
            )
            if system_id:
                return system_id
        token_match = re.match(r"^/(?:j|api/public/journeys)/([^/]+)", path)
        if token_match:
            return db.scalar(
                select(Journey.system_id)
                .where(Journey.public_token == token_match.group(1))
                .execution_options(bypass_recruitment_scope=True)
            )
        attendance_match = re.match(r"^/(?:recruit-attendance|api/public/recruit-attendance)/([^/]+)", path)
        if attendance_match:
            return db.scalar(
                select(Journey.system_id)
                .join(RecruitAttendanceAccess, RecruitAttendanceAccess.journey_id == Journey.id)
                .where(RecruitAttendanceAccess.token == attendance_match.group(1))
                .execution_options(bypass_recruitment_scope=True)
            )
        # Preserve the established permanent evaluator/management URLs while
        # this installation contains a single recruitment. Once an owner adds
        # more workspaces, selecting one supplies the explicit workspace cookie.
        if path != "/" and not path.startswith("/api/platform"):
            systems = list(db.scalars(
                select(AssessmentSystem.id)
                .where(AssessmentSystem.status == "active")
                .limit(2)
                .execution_options(bypass_recruitment_scope=True)
            ))
            if len(systems) == 1:
                return systems[0]
    return None


def _runtime_for_system(system_id: str):
    with SessionLocal() as db:
        system = db.scalar(
            select(AssessmentSystem).where(AssessmentSystem.id == system_id)
            .execution_options(bypass_recruitment_scope=True)
        )
        if not system:
            return None
        record = db.scalar(
            select(AssessmentSystemVersion).where(
                AssessmentSystemVersion.system_id == system.id,
                AssessmentSystemVersion.version == system.published_version,
            ).execution_options(bypass_recruitment_scope=True)
        )
        return AssessmentSystemDefinition.model_validate(loads(record.definition_json, {})) if record else None


@app.middleware("http")
async def prevent_stale_frontend_assets(request: Request, call_next):
    """Keep deployments from mixing cached HTML/JS files from different releases."""
    if request.url.path == "/health/live":
        return await call_next(request)
    if database_startup_error and request.url.path != "/health/ready":
        return JSONResponse(
            status_code=503,
            content={"detail": "The data service is temporarily unavailable.", "ready": False},
            headers={"Retry-After": "15"},
        )
    system_id = _request_system_id(request)
    system_token = select_system(system_id)
    runtime_token = None
    try:
        definition = _runtime_for_system(system_id) if system_id else None
        if definition:
            runtime_token = activate_assessment_definition(definition)
        response = await call_next(request)
        if system_id and not request.cookies.get(SYSTEM_COOKIE):
            with SessionLocal() as cookie_db:
                slug = cookie_db.scalar(
                    select(AssessmentSystem.slug).where(AssessmentSystem.id == system_id)
                    .execution_options(bypass_recruitment_scope=True)
                )
            if slug:
                set_system_cookie(response, slug)
    finally:
        reset_assessment_definition(runtime_token)
        reset_system(system_token)
    path = request.url.path
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    elif path == "/" or path == "/admin" or path.startswith("/admin/") or path == "/configure" or path == "/view" or path == "/evaluate" or path.startswith("/j/") or path.startswith("/recruit-attendance/") or re.match(r"^/[^/]+(?:/|$)", path):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health/live", include_in_schema=False)
def health_live():
    return {"status": "ok", "databaseReady": database_startup_error is None}


@app.get("/health/ready", include_in_schema=False)
def health_ready():
    global database_startup_error
    try:
        with SessionLocal() as db:
            db.connection().exec_driver_sql("select 1")
            revision = db.connection().exec_driver_sql("select version_num from alembic_version").scalar_one()
            if revision != "0017_room_evaluator_locks":
                raise RuntimeError(
                    f"Database migration is {revision!r}, expected '0017_room_evaluator_locks'."
                )
        database_startup_error = None
        return {"status": "ready"}
    except Exception as exc:
        database_startup_error = str(exc)
        raise HTTPException(status_code=503, detail=f"Database not ready: {exc}") from exc


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/admin", include_in_schema=False)
@app.get("/admin/{path:path}", include_in_schema=False)
def admin_app(path: str = ""):
    del path
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/configure", include_in_schema=False)
def configure_assessment_system():
    return FileResponse(STATIC_DIR / "configurator.html")


@app.get("/j/{token}", include_in_schema=False)
def evaluator_app(token: str):
    del token
    profile = next((item for item in active_assessment_definition().accessProfiles if item.key == "assessor"), None)
    if profile and not profile.enabled:
        raise HTTPException(status_code=404, detail="The assessor portal is disabled.")
    return FileResponse(STATIC_DIR / "evaluator.html")


@app.get("/evaluate", include_in_schema=False)
def current_evaluator_app():
    profile = next((item for item in active_assessment_definition().accessProfiles if item.key == "assessor"), None)
    if profile and not profile.enabled:
        raise HTTPException(status_code=404, detail="The assessor portal is disabled.")
    return FileResponse(STATIC_DIR / "evaluator.html")


@app.get("/view", include_in_schema=False)
def read_only_management_app():
    if not active_assessment_definition().features.managementPortal:
        raise HTTPException(status_code=404, detail="The management portal is disabled.")
    return FileResponse(STATIC_DIR / "viewer.html")


@app.get("/recruit-attendance/{token}", include_in_schema=False)
def recruit_attendance_app(token: str):
    del token
    if not active_assessment_definition().features.attendancePortal:
        raise HTTPException(status_code=404, detail="The attendance portal is disabled.")
    return FileResponse(STATIC_DIR / "recruit_attendance.html")


def _workspace_file(workspace_slug: str, filename: str):
    """Serve a workspace page and pin subsequent API calls to its tenant."""
    with SessionLocal() as db:
        system = db.scalar(
            select(AssessmentSystem)
            .where(AssessmentSystem.slug == workspace_slug, AssessmentSystem.status == "active")
            .execution_options(bypass_recruitment_scope=True)
        )
    if not system:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    response = FileResponse(STATIC_DIR / filename)
    set_system_cookie(response, system.slug)
    return response


@app.get("/{workspace_slug}", include_in_schema=False)
def workspace_home(workspace_slug: str):
    return _workspace_file(workspace_slug, "admin.html")


@app.get("/{workspace_slug}/admin", include_in_schema=False)
@app.get("/{workspace_slug}/admin/{path:path}", include_in_schema=False)
def workspace_admin_app(workspace_slug: str, path: str = ""):
    del path
    return _workspace_file(workspace_slug, "admin.html")


@app.get("/{workspace_slug}/configure", include_in_schema=False)
def workspace_configurator(workspace_slug: str):
    return _workspace_file(workspace_slug, "configurator.html")


@app.get("/{workspace_slug}/evaluate", include_in_schema=False)
def workspace_evaluator(workspace_slug: str):
    return _workspace_file(workspace_slug, "evaluator.html")


@app.get("/{workspace_slug}/view", include_in_schema=False)
def workspace_management(workspace_slug: str):
    return _workspace_file(workspace_slug, "viewer.html")


@app.get("/{workspace_slug}/attendance/{token}", include_in_schema=False)
def workspace_attendance(workspace_slug: str, token: str):
    with SessionLocal() as db:
        valid = db.scalar(
            select(RecruitAttendanceAccess.journey_id)
            .join(Journey, Journey.id == RecruitAttendanceAccess.journey_id)
            .join(AssessmentSystem, AssessmentSystem.id == Journey.system_id)
            .where(AssessmentSystem.slug == workspace_slug, RecruitAttendanceAccess.token == token)
            .execution_options(bypass_recruitment_scope=True)
        )
    if not valid:
        raise HTTPException(status_code=404, detail="Attendance link not found.")
    return _workspace_file(workspace_slug, "recruit_attendance.html")
