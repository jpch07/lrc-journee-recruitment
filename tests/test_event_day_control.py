from __future__ import annotations

from app import routes_admin


def _login(client) -> str:
    response = client.post(
        "/api/admin/login",
        json={"password": "test-password", "display_name": "Event Admin"},
    )
    assert response.status_code == 200
    return response.json()["csrfToken"]


def _journey(client, csrf: str) -> dict:
    response = client.post(
        "/api/admin/journeys",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Protected Journee", "event_date": "2026-08-10"},
    )
    assert response.status_code == 200
    return response.json()


def test_one_action_starts_tracks_and_ends_six_hour_journee(client, monkeypatch):
    dispatched: list[tuple[int, str]] = []
    cancelled: list[str] = []
    monkeypatch.setattr(routes_admin, "is_configured", lambda: True)
    monkeypatch.setattr(
        routes_admin,
        "dispatch_monitor",
        lambda duration, activation: dispatched.append((duration, activation)),
    )
    monkeypatch.setattr(
        routes_admin,
        "find_monitor_run",
        lambda activation: {
            "id": 203,
            "display_title": f"Journee protection {activation}",
            "status": "in_progress",
            "conclusion": None,
            "html_url": "https://github.test/actions/runs/203",
        },
    )
    monkeypatch.setattr(routes_admin, "cancel_monitor", lambda run_id: cancelled.append(run_id))

    csrf = _login(client)
    journey = _journey(client, csrf)
    path = f"/api/admin/journeys/{journey['id']}/event-day-protection"
    started = client.post(
        f"{path}/start",
        headers={"X-CSRF-Token": csrf},
        json={"duration_hours": 6},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "starting"
    assert dispatched and dispatched[0][0] == 6

    status = client.get(path)
    assert status.status_code == 200
    assert status.json()["status"] == "active"
    assert status.json()["durationHours"] == 6
    assert status.json()["runUrl"].endswith("/203")
    assert client.get(f"/api/admin/journeys/{journey['id']}").json()["status"] == "active"

    ended = client.post(
        f"{path}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "Event finished"},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["protection"]["status"] == "stopped"
    assert ended.json()["journeyStatus"] == "completed"
    assert cancelled == ["203"]


def test_twelve_hour_start_and_incident_restart_are_one_action(client, monkeypatch):
    dispatched: list[tuple[int, str]] = []
    monkeypatch.setattr(routes_admin, "is_configured", lambda: True)
    monkeypatch.setattr(
        routes_admin,
        "dispatch_monitor",
        lambda duration, activation: dispatched.append((duration, activation)),
    )
    monkeypatch.setattr(routes_admin, "find_monitor_run", lambda _activation: None)
    monkeypatch.setattr(routes_admin, "cancel_monitor", lambda _run_id: None)

    csrf = _login(client)
    journey = _journey(client, csrf)
    path = f"/api/admin/journeys/{journey['id']}/event-day-protection"
    response = client.post(
        f"{path}/start",
        headers={"X-CSRF-Token": csrf},
        json={"duration_hours": 12},
    )
    assert response.status_code == 200
    first_activation = dispatched[-1][1]

    response = client.post(
        f"{path}/restart",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "Monitor incident"},
    )
    assert response.status_code == 200, response.text
    assert dispatched[-1][0] == 12
    assert dispatched[-1][1] != first_activation


def test_protection_mutations_require_csrf(client, monkeypatch):
    monkeypatch.setattr(routes_admin, "is_configured", lambda: True)
    csrf = _login(client)
    journey = _journey(client, csrf)
    response = client.post(
        f"/api/admin/journeys/{journey['id']}/event-day-protection/start",
        json={"duration_hours": 6},
    )
    assert response.status_code == 403
