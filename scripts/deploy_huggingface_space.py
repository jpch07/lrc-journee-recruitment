from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_ROOT_FILES = {"Dockerfile", ".dockerignore", "requirements.txt", "alembic.ini", "README.md"}
STATIC_SUFFIXES = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2"}


def is_deployment_file(name: str) -> bool:
    """Allow application sources/assets, never local data or operator tooling."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        return False
    if name in DEPLOYMENT_ROOT_FILES:
        return True
    if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
        return False
    if len(path.parts) == 2 and path.parts[0] == "app" and path.suffix == ".py":
        return True
    if path.parts[:2] == ("app", "static") and path.suffix.lower() in STATIC_SUFFIXES:
        return True
    if name in {"migrations/env.py", "migrations/script.py.mako"}:
        return True
    return len(path.parts) == 3 and path.parts[:2] == ("migrations", "versions") and path.suffix == ".py"


def deployment_files(root: Path) -> list[Path]:
    """Fail closed without Git: an accidental local folder must not be uploaded."""
    root = root.resolve()
    result = subprocess.run(
        ["git", "ls-files", "--cached", "-z"], cwd=root,
        check=True, capture_output=True,
    )
    files = []
    for name in result.stdout.decode("utf-8").split("\0"):
        if not name or not is_deployment_file(name):
            continue
        path = root / name
        # Even a tracked symlink must not pull a credential file from outside the
        # deployment source tree (or elsewhere inside it) into a public Space.
        if path.resolve() != path.absolute():
            raise ValueError(f"Refusing symlinked deployment file: {name}")
        if path.is_file():
            files.append(Path(name))
    required = {"Dockerfile", "requirements.txt", "alembic.ini", "app/main.py"}
    if not required.issubset({path.as_posix() for path in files}):
        raise ValueError("Tracked deployment sources are incomplete; nothing was uploaded")
    return sorted(set(files))


def stage_deployment(root: Path, destination: Path) -> list[Path]:
    files = deployment_files(root)
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    return files


def main() -> None:
    from huggingface_hub import HfApi, get_token

    parser = argparse.ArgumentParser(description="Deploy the LRC Journee Recruitment Docker Space.")
    parser.add_argument("--repo-id", default="lrc203/journee-recruitment")
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN") or get_token()
    if not token:
        raise SystemExit("Set HF_TOKEN before deployment.")
    with tempfile.TemporaryDirectory(prefix="evalday-deployment-") as temporary:
        # Never point upload_folder at the working tree: it contains production
        # backups, photos, reports and possibly local credentials under outputs/
        # and tmp/. Only tracked, explicitly allowlisted sources are staged.
        stage_deployment(ROOT, Path(temporary))
        api = HfApi(token=token)
        api.create_repo(
            repo_id=args.repo_id,
            repo_type="space",
            space_sdk="docker",
            private=args.private,
            exist_ok=True,
            token=token,
        )
        api.upload_folder(
            repo_id=args.repo_id,
            repo_type="space",
            folder_path=temporary,
            path_in_repo=".",
            commit_message="Deploy application sources",
            token=token,
        )
    print(f"Deployed: https://huggingface.co/spaces/{args.repo_id}")


if __name__ == "__main__":
    main()
