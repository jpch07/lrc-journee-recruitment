from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from scripts import deploy_huggingface_space as deploy


@pytest.mark.parametrize("name", [
    "outputs/production-backups/accounts.json", "tmp/database-url.txt",
    "data/journee.db", ".env", ".env.production", ".vscode/settings.json",
    "scripts/hash_password.py", "tests/test_workflow.py", "docs/ARCHITECTURE.md",
    "app/.env", "app/__pycache__/config.pyc", "app/static/.env.js",
    "app/static/../../outputs/accounts.json", "/app/main.py", "app\\main.py",
    "app/static/credentials.json", "requirements-dev.txt",
])
def test_sensitive_or_non_runtime_paths_are_never_deployed(name):
    assert deploy.is_deployment_file(name) is False


@pytest.mark.parametrize("name", [
    "Dockerfile", ".dockerignore", "requirements.txt", "README.md", "alembic.ini",
    "app/main.py", "app/static/styles.css", "app/static/evaluator.js",
    "migrations/env.py", "migrations/script.py.mako", "migrations/versions/0018_dynamic_general_factors.py",
])
def test_only_runtime_paths_are_eligible(name):
    assert deploy.is_deployment_file(name) is True


def test_staging_intersects_git_tracked_files_and_allowlist(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "stage"
    tracked = ["Dockerfile", "requirements.txt", "alembic.ini", "app/main.py", "outputs/accounts.json", "tmp/secrets.txt"]
    for name in tracked + ["app/untracked_credentials.py", "app/static/untracked_photo.png"]:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("example", encoding="utf-8")

    def git_files(command, **kwargs):
        assert command == ["git", "ls-files", "--cached", "-z"]
        assert kwargs["check"] is True
        return CompletedProcess(command, 0, stdout=("\0".join(tracked) + "\0").encode())

    monkeypatch.setattr(deploy.subprocess, "run", git_files)
    files = deploy.stage_deployment(source, target)
    expected = {"Dockerfile", "requirements.txt", "alembic.ini", "app/main.py"}
    assert {path.as_posix() for path in files} == expected
    assert {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()} == expected


def test_incomplete_sources_fail_before_any_remote_action(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy.subprocess, "run", lambda *args, **kwargs: CompletedProcess(args, 0, stdout=b"README.md\0"))
    with pytest.raises(ValueError, match="incomplete"):
        deploy.deployment_files(tmp_path)
