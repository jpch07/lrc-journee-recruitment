from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


def _admin_login(client) -> str:
    response = client.post(
        "/api/admin/login",
        json={"password": "test-password", "display_name": "Admin Device"},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrfToken"]


def _attendance_login(client, path: str, name: str) -> str:
    token = path.rsplit("/", 1)[-1]
    landing = client.get(f"/api/public/recruit-attendance/{token}")
    assert landing.status_code == 200, landing.text
    response = client.post(
        f"/api/public/recruit-attendance/{token}/session",
        json={"display_name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrfToken"]


def _photo_bytes(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 100), color=color).save(output, format="PNG")
    return output.getvalue()


def test_attendance_operator_can_add_replace_and_remove_recruit_photo(client):
    admin_csrf = _admin_login(client)
    admin_headers = {"X-CSRF-Token": admin_csrf}
    journey = client.post(
        "/api/admin/journeys",
        headers=admin_headers,
        json={"name": "Photo Attendance", "event_date": "2026-08-14"},
    ).json()
    recruit = client.post(
        f"/api/admin/journeys/{journey['id']}/recruits",
        headers=admin_headers,
        json={"name": "Recruit Photo"},
    ).json()
    path = client.get(f"/api/admin/journeys/{journey['id']}").json()["recruitAttendancePath"]

    with TestClient(app) as attendance_device:
        csrf = _attendance_login(attendance_device, path, "Door Photographer")
        url = f"/api/recruit-attendance/recruits/{recruit['id']}/photo"
        first_photo = _photo_bytes((180, 20, 40))

        missing_csrf = attendance_device.post(
            url,
            files={"photo": ("first.png", first_photo, "image/png")},
        )
        assert missing_csrf.status_code == 403

        uploaded = attendance_device.post(
            url,
            headers={"X-CSRF-Token": csrf},
            files={"photo": ("first.png", first_photo, "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["hasPhoto"] is True
        first_version = uploaded.json()["version"]
        served = attendance_device.get(url)
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/webp"

        replaced = attendance_device.post(
            url,
            headers={"X-CSRF-Token": csrf},
            files={"photo": ("replacement.png", _photo_bytes((20, 80, 180)), "image/png")},
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["version"] == first_version + 1

        removed = attendance_device.delete(url, headers={"X-CSRF-Token": csrf})
        assert removed.status_code == 200, removed.text
        assert removed.json()["hasPhoto"] is False
        assert removed.json()["version"] == first_version + 2
        assert attendance_device.get(url).status_code == 404

    admin_detail = client.get(f"/api/admin/journeys/{journey['id']}").json()
    admin_recruit = next(item for item in admin_detail["recruits"] if item["id"] == recruit["id"])
    assert admin_recruit["hasPhoto"] is False


def test_attendance_link_is_scoped_and_merges_concurrent_field_changes(client):
    admin_csrf = _admin_login(client)
    admin_headers = {"X-CSRF-Token": admin_csrf}
    journey = client.post(
        "/api/admin/journeys",
        headers=admin_headers,
        json={"name": "Concurrent Attendance", "event_date": "2026-08-12"},
    ).json()
    recruit = client.post(
        f"/api/admin/journeys/{journey['id']}/recruits",
        headers=admin_headers,
        json={"name": "Recruit One"},
    ).json()
    detail = client.get(f"/api/admin/journeys/{journey['id']}").json()
    path = detail["recruitAttendancePath"]

    with TestClient(app) as first_device, TestClient(app) as second_device:
        first_csrf = _attendance_login(first_device, path, "Attendance One")
        second_csrf = _attendance_login(second_device, path, "Attendance Two")
        first_headers = {"X-CSRF-Token": first_csrf}
        second_headers = {"X-CSRF-Token": second_csrf}

        roster = first_device.get("/api/recruit-attendance/recruits")
        assert roster.status_code == 200
        assert set(roster.json()) == {"journey", "recruits", "serverTime"}
        assert first_device.get("/api/admin/journeys").status_code == 401
        first_version = roster.json()["recruits"][0]["version"]
        second_version = second_device.get("/api/recruit-attendance/recruits").json()["recruits"][0]["version"]
        assert first_version == second_version == recruit["version"]

        phone_save = first_device.patch(
            f"/api/recruit-attendance/recruits/{recruit['id']}",
            headers=first_headers,
            json={"base_version": first_version, "phone_number": "70123456"},
        )
        assert phone_save.status_code == 200, phone_save.text
        assert phone_save.json()["version"] == first_version + 1

        stale_birth_date = second_device.patch(
            f"/api/recruit-attendance/recruits/{recruit['id']}",
            headers=second_headers,
            json={"base_version": second_version, "date_of_birth": "2004-03-02"},
        )
        assert stale_birth_date.status_code == 409

        latest = second_device.get("/api/recruit-attendance/recruits").json()["recruits"][0]
        birth_date_save = second_device.patch(
            f"/api/recruit-attendance/recruits/{recruit['id']}",
            headers=second_headers,
            json={"base_version": latest["version"], "date_of_birth": "2004-03-02"},
        )
        assert birth_date_save.status_code == 200, birth_date_save.text

        final = first_device.get("/api/recruit-attendance/recruits").json()["recruits"][0]
        assert final["phoneNumber"] == "70123456"
        assert final["dateOfBirth"] == "2004-03-02"

        admin_latest = client.get(f"/api/admin/journeys/{journey['id']}").json()
        admin_recruit = next(item for item in admin_latest["recruits"] if item["id"] == recruit["id"])
        present_save = client.patch(
            f"/api/admin/journeys/{journey['id']}/recruits/{recruit['id']}/attendance",
            headers=admin_headers,
            json={
                "base_version": admin_recruit["version"],
                "present": True,
                "arrival_time": "2026-08-12T07:45:00+03:00",
            },
        )
        assert present_save.status_code == 200, present_save.text
        assert present_save.json()["present"] is True


def test_rotating_or_completing_journey_closes_attendance_access(client):
    csrf = _admin_login(client)
    headers = {"X-CSRF-Token": csrf}
    journey = client.post(
        "/api/admin/journeys",
        headers=headers,
        json={"name": "Rotating Link", "event_date": "2026-08-13"},
    ).json()
    detail = client.get(f"/api/admin/journeys/{journey['id']}").json()
    old_path = detail["recruitAttendancePath"]

    with TestClient(app) as attendance_device:
        _attendance_login(attendance_device, old_path, "Door Volunteer")
        rotated = client.post(
            f"/api/admin/journeys/{journey['id']}/rotate-recruit-attendance-link",
            headers=headers,
        )
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["recruitAttendancePath"] != old_path
        old_token = old_path.rsplit("/", 1)[-1]
        assert attendance_device.get(f"/api/public/recruit-attendance/{old_token}").status_code == 404
        assert attendance_device.get("/api/recruit-attendance/recruits").status_code == 401

    refreshed = client.get(f"/api/admin/journeys/{journey['id']}").json()
    response = client.patch(
        f"/api/admin/journeys/{journey['id']}",
        headers=headers,
        json={"status": "completed", "base_version": refreshed["version"]},
    )
    assert response.status_code == 200, response.text
    new_token = rotated.json()["recruitAttendancePath"].rsplit("/", 1)[-1]
    assert client.get(f"/api/public/recruit-attendance/{new_token}").status_code == 404
