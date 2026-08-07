from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR, settings
from .auth import ensure_owner_account
from .db import SessionLocal, initialize_database
from .routes_admin import router as admin_router
from .routes_evaluator import router as evaluator_router
from .routes_attendance import router as attendance_router
from .routes_auth import router as auth_router
from .routes_viewer import router as viewer_router
from .rubric import validate_rubrics


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate_production()
    validate_rubrics()
    initialize_database()
    with SessionLocal() as db:
        ensure_owner_account(db)
        db.commit()
    yield


app = FastAPI(
    title="LRC Journee Recruitment",
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
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def prevent_stale_frontend_assets(request: Request, call_next):
    """Keep deployments from mixing cached HTML/JS files from different releases."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    elif path == "/" or path == "/admin" or path.startswith("/admin/") or path == "/view" or path == "/evaluate" or path.startswith("/j/") or path.startswith("/recruit-attendance/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health/live", include_in_schema=False)
def health_live():
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def health_ready():
    try:
        with SessionLocal() as db:
            db.connection().exec_driver_sql("select 1")
            revision = db.connection().exec_driver_sql("select version_num from alembic_version").scalar_one()
            if revision != "0010_visible_managed_passwords":
                raise RuntimeError(
                    f"Database migration is {revision!r}, expected '0010_visible_managed_passwords'."
                )
        return {"status": "ready"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database not ready: {exc}") from exc


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/admin")


@app.get("/admin", include_in_schema=False)
@app.get("/admin/{path:path}", include_in_schema=False)
def admin_app(path: str = ""):
    del path
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/j/{token}", include_in_schema=False)
def evaluator_app(token: str):
    del token
    return FileResponse(STATIC_DIR / "evaluator.html")


@app.get("/evaluate", include_in_schema=False)
def current_evaluator_app():
    return FileResponse(STATIC_DIR / "evaluator.html")


@app.get("/view", include_in_schema=False)
def read_only_management_app():
    return FileResponse(STATIC_DIR / "viewer.html")


@app.get("/recruit-attendance/{token}", include_in_schema=False)
def recruit_attendance_app(token: str):
    del token
    return FileResponse(STATIC_DIR / "recruit_attendance.html")
