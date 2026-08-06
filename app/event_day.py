from __future__ import annotations

from typing import Any

import httpx

from .config import settings


class ProtectionControlError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.github_actions_token and settings.github_repository and settings.github_workflow)


def _headers() -> dict[str, str]:
    if not is_configured():
        raise ProtectionControlError("Event-day protection is not configured on this server.")
    return {
        "Authorization": f"Bearer {settings.github_actions_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _workflow_url(suffix: str = "") -> str:
    base = (
        f"https://api.github.com/repos/{settings.github_repository}/actions/"
        f"workflows/{settings.github_workflow}"
    )
    return base + suffix


def dispatch_monitor(duration_hours: int, activation_id: str) -> None:
    try:
        response = httpx.post(
            _workflow_url("/dispatches"),
            headers=_headers(),
            json={
                "ref": "main",
                "inputs": {
                    "duration_hours": str(duration_hours),
                    "activation_id": activation_id,
                },
            },
            timeout=20,
        )
        response.raise_for_status()
    except (httpx.HTTPError, ProtectionControlError) as exc:
        detail = getattr(getattr(exc, "response", None), "text", "")
        raise ProtectionControlError(f"GitHub could not start the protection monitors. {detail}".strip()) from exc


def find_monitor_run(activation_id: str) -> dict[str, Any] | None:
    try:
        response = httpx.get(
            _workflow_url("/runs"),
            headers=_headers(),
            params={"event": "workflow_dispatch", "per_page": 30},
            timeout=20,
        )
        response.raise_for_status()
    except (httpx.HTTPError, ProtectionControlError) as exc:
        raise ProtectionControlError("GitHub protection status is temporarily unavailable.") from exc
    for run in response.json().get("workflow_runs", []):
        if activation_id in (run.get("display_title") or ""):
            try:
                jobs_response = httpx.get(
                    f"https://api.github.com/repos/{settings.github_repository}/actions/runs/{run['id']}/jobs",
                    headers=_headers(),
                    params={"per_page": 100},
                    timeout=20,
                )
                jobs_response.raise_for_status()
                run["jobs"] = jobs_response.json().get("jobs", [])
            except httpx.HTTPError:
                run["jobs"] = []
            return run
    return None


def cancel_monitor(run_id: str) -> None:
    try:
        response = httpx.post(
            f"https://api.github.com/repos/{settings.github_repository}/actions/runs/{run_id}/cancel",
            headers=_headers(),
            timeout=20,
        )
        if response.status_code not in {202, 409}:
            response.raise_for_status()
    except (httpx.HTTPError, ProtectionControlError) as exc:
        raise ProtectionControlError("GitHub could not stop the protection monitors.") from exc
