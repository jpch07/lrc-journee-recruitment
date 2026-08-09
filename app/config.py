from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
STATIC_DIR = ROOT_DIR / "app" / "static"
DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_password_hash: str
    admin_password: str
    session_secret: str
    cookie_secure: bool
    session_hours: int
    environment: str
    test_tools_enabled: bool
    github_actions_token: str = ""
    github_repository: str = "jpch07/lrc-journee-recruitment"
    github_workflow: str = "event-day-watchdog.yml"
    recruit_sheet_id: str = "1jlVdC5Hwqy78ivZ2JhxEBcJv3SUWsJ6F"
    recruit_sheet_name: str = "List of Recruits"
    recruit_sheet_sync_seconds: int = 300
    timezone: str = "Asia/Beirut"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def allow_test_tools(self) -> bool:
        return self.test_tools_enabled and not self.is_production

    def validate_production(self) -> None:
        """Refuse to start a production instance with development-safe defaults."""
        if not self.is_production:
            return
        problems: list[str] = []
        if self.database_url.startswith("sqlite"):
            problems.append("LRC_DATABASE_URL must point to external PostgreSQL")
        if not self.admin_password_hash.startswith("$argon2"):
            problems.append("LRC_JOURNEE_ADMIN_PASSWORD_HASH must contain an Argon2 hash")
        if len(self.session_secret) < 32 or self.session_secret == "local-development-secret-change-me":
            problems.append("LRC_JOURNEE_SESSION_SECRET must be a unique value of at least 32 characters")
        if not self.cookie_secure:
            problems.append("LRC_JOURNEE_COOKIE_SECURE must be true")
        if problems:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    database_url = os.getenv("LRC_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
    if not database_url:
        database_url = f"sqlite:///{(DATA_DIR / 'journee.db').as_posix()}"
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgres://") :]
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]

    return Settings(
        database_url=database_url,
        admin_password_hash=os.getenv("LRC_JOURNEE_ADMIN_PASSWORD_HASH", ""),
        admin_password=os.getenv("LRC_JOURNEE_ADMIN_PASSWORD", "admin"),
        session_secret=os.getenv("LRC_JOURNEE_SESSION_SECRET", "local-development-secret-change-me"),
        cookie_secure=_bool_env("LRC_JOURNEE_COOKIE_SECURE", False),
        session_hours=int(os.getenv("LRC_JOURNEE_SESSION_HOURS", "12")),
        environment=os.getenv("LRC_JOURNEE_ENV", "development"),
        test_tools_enabled=_bool_env("LRC_JOURNEE_TEST_TOOLS", False),
        github_actions_token=os.getenv("LRC_GITHUB_ACTIONS_TOKEN", ""),
        github_repository=os.getenv("LRC_GITHUB_REPOSITORY", "jpch07/lrc-journee-recruitment"),
        github_workflow=os.getenv("LRC_GITHUB_WORKFLOW", "event-day-watchdog.yml"),
        recruit_sheet_id=os.getenv("LRC_RECRUIT_SHEET_ID", "1jlVdC5Hwqy78ivZ2JhxEBcJv3SUWsJ6F").strip(),
        recruit_sheet_name=os.getenv("LRC_RECRUIT_SHEET_NAME", "List of Recruits").strip(),
        recruit_sheet_sync_seconds=max(60, int(os.getenv("LRC_RECRUIT_SHEET_SYNC_SECONDS", "300"))),
    )


settings = load_settings()
