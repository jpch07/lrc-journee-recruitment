from __future__ import annotations

import argparse
import os
from pathlib import Path

from huggingface_hub import HfApi


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy the LRC Journee Recruitment Docker Space.")
    parser.add_argument("--repo-id", default="lrc203/journee-recruitment")
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not token:
        raise SystemExit("Set HF_TOKEN before deployment.")
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
        folder_path=str(ROOT),
        path_in_repo=".",
        ignore_patterns=[
            ".git/*", ".venv/*", "venv/*", "__pycache__/*", "**/__pycache__/*",
            "*.pyc", ".env", ".env.*", "data/*", ".pytest_cache/*", "tests/*",
            "playwright-report/*", "test-results/*",
            "scripts/apply_2_august_details.py", "scripts/apply_2_august_pm_details.py",
            "scripts/import_2_august_phones.py",
        ],
        commit_message="Deploy LRC Journee Recruitment",
        token=token,
    )
    print(f"Deployed: https://huggingface.co/spaces/{args.repo_id}")


if __name__ == "__main__":
    main()
