from __future__ import annotations


def _login(client, username="JP Chaaya", password="test-password"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def test_named_permissions_protect_admin_results_and_attendance(client):
    owner = _login(client)
    headers = {"X-CSRF-Token": owner["csrfToken"]}
    journey = client.post("/api/admin/journeys", headers=headers,
                          json={"name": "Permissions", "event_date": "2026-09-09"}).json()
    created = client.post("/api/auth/accounts", headers=headers, json={
        "username": "Permission Tester", "password": "temporary-password",
        "evaluator_role": "dossard",
    })
    assert created.status_code == 200, created.text
    account = created.json()
    updated = client.patch(f"/api/auth/accounts/{account['id']}", headers=headers, json={
        "can_results": True,
        "attendance_journey_ids": [journey["id"]],
        "base_version": account["version"],
    })
    assert updated.status_code == 200, updated.text

    user = _login(client, "Permission Tester", "temporary-password")
    assert client.get("/api/admin/journeys").status_code == 403
    assert client.get("/api/view/journeys").status_code == 200
    detail = client.get(f"/api/view/journeys/{journey['id']}")
    assert detail.status_code == 200

    owner = _login(client)
    journey_detail = client.get(f"/api/admin/journeys/{journey['id']}").json()
    token = journey_detail["recruitAttendancePath"].rsplit("/", 1)[-1]
    user = _login(client, "Permission Tester", "temporary-password")
    selected = client.post(f"/api/public/recruit-attendance/{token}/select",
                           headers={"X-CSRF-Token": user["csrfToken"]})
    assert selected.status_code == 200, selected.text


def test_marita_is_not_available_in_the_global_account_directory(client):
    names = [item["username"].casefold() for item in client.get("/api/auth/usernames").json()]
    assert "marita" not in names
