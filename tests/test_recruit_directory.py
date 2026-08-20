from __future__ import annotations

from datetime import date

import app.recruit_directory as recruit_directory


def _admin_login(client) -> str:
    response = client.post(
        "/api/admin/login",
        json={"password": "test-password", "display_name": "Directory Admin"},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrfToken"]


def _rows() -> list[dict]:
    return [
        {
            "source_key": "a" * 64,
            "source_row": 2,
            "name": "Maria Issa",
            "phone_number": "70 227733",
            "date_of_birth": date(2002, 12, 18),
            "date_of_birth_raw": "Dec18 2002",
        },
        {
            "source_key": "b" * 64,
            "source_row": 3,
            "name": "Karl Nassar",
            "phone_number": "71 000000",
            "date_of_birth": None,
            "date_of_birth_raw": "25 December",
        },
    ]


def test_sheet_directory_adds_recruit_with_phone_and_birth_date(client, monkeypatch):
    monkeypatch.setattr(recruit_directory, "fetch_directory_rows", _rows)
    csrf = _admin_login(client)
    headers = {"X-CSRF-Token": csrf}
    journey = client.post(
        "/api/admin/journeys",
        headers=headers,
        json={"name": "Directory Journee", "event_date": "2026-08-15"},
    ).json()

    directory = client.get("/api/admin/recruit-directory")
    assert directory.status_code == 200, directory.text
    assert [item["name"] for item in directory.json()["items"]] == ["Karl Nassar", "Maria Issa"]
    maria = next(item for item in directory.json()["items"] if item["name"] == "Maria Issa")
    assert maria["phoneNumber"] == "70 227733"
    assert maria["dateOfBirth"] == "2002-12-18"
    assert maria["dateOfBirthSource"] == "Dec18 2002"

    added = client.post(
        f"/api/admin/journeys/{journey['id']}/recruits/from-directory",
        headers=headers,
        json={"directory_id": maria["id"]},
    )
    assert added.status_code == 200, added.text
    assert added.json()["directoryId"] == maria["id"]
    assert added.json()["phoneNumber"] == "70 227733"
    assert added.json()["dateOfBirth"] == "2002-12-18"
    duplicate = client.post(
        f"/api/admin/journeys/{journey['id']}/recruits/from-directory",
        headers=headers,
        json={"directory_id": maria["id"]},
    )
    assert duplicate.status_code == 409

    off_list = client.post(
        f"/api/admin/journeys/{journey['id']}/recruits",
        headers=headers,
        json={"name": "Admin Off List", "phone_number": "70111222"},
    )
    assert off_list.status_code == 200, off_list.text
    assert off_list.json()["directoryId"] is None


def test_attendance_operator_can_select_directory_but_cannot_create_off_list(client, monkeypatch):
    monkeypatch.setattr(recruit_directory, "fetch_directory_rows", _rows)
    admin_csrf = _admin_login(client)
    admin_headers = {"X-CSRF-Token": admin_csrf}
    journey = client.post(
        "/api/admin/journeys",
        headers=admin_headers,
        json={"name": "Door Directory", "event_date": "2026-08-16"},
    ).json()
    detail = client.get(f"/api/admin/journeys/{journey['id']}").json()
    token = detail["recruitAttendancePath"].rsplit("/", 1)[-1]
    login = client.post(
        f"/api/public/recruit-attendance/{token}/session",
        json={"display_name": "Door Operator"},
    )
    assert login.status_code == 200, login.text
    headers = {"X-CSRF-Token": login.json()["csrfToken"]}

    directory = client.get("/api/recruit-attendance/directory")
    assert directory.status_code == 200, directory.text
    maria = next(item for item in directory.json()["items"] if item["name"] == "Maria Issa")
    added = client.post(
        "/api/recruit-attendance/recruits/from-directory",
        headers=headers,
        json={"directory_id": maria["id"]},
    )
    assert added.status_code == 200, added.text
    assert added.json()["phoneNumber"] == "70 227733"
    assert added.json()["dateOfBirth"] == "2002-12-18"

    manual = client.post(
        "/api/recruit-attendance/recruits",
        headers=headers,
        json={"name": "Forbidden Off List"},
    )
    assert manual.status_code == 405


def test_sync_failure_uses_last_successful_database_cache(client, monkeypatch):
    monkeypatch.setattr(recruit_directory, "fetch_directory_rows", _rows)
    csrf = _admin_login(client)
    headers = {"X-CSRF-Token": csrf}
    assert client.get("/api/admin/recruit-directory").status_code == 200

    def fail():
        raise RuntimeError("Google unavailable")

    monkeypatch.setattr(recruit_directory, "fetch_directory_rows", fail)
    response = client.post("/api/admin/recruit-directory/sync", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["stale"] is True
    assert len(response.json()["items"]) == 2
    assert "Google unavailable" in response.json()["lastError"]


def test_birth_date_parser_preserves_only_complete_dates():
    assert recruit_directory._parse_birth_date("Dec18 2002") == date(2002, 12, 18)
    assert recruit_directory._parse_birth_date("21 December 2007") == date(2007, 12, 21)
    assert recruit_directory._parse_birth_date("03-Oct-2005") == date(2005, 10, 3)
    assert recruit_directory._parse_birth_date("12/18/2002") == date(2002, 12, 18)
    assert recruit_directory._parse_birth_date("12-18-2002") == date(2002, 12, 18)
    assert recruit_directory._parse_birth_date("25 December") is None


def test_sheet_contact_update_reaches_existing_attendance_record(client, monkeypatch):
    rows = _rows()
    rows[1]["date_of_birth_raw"] = ""
    monkeypatch.setattr(recruit_directory, "fetch_directory_rows", lambda: rows)
    csrf = _admin_login(client)
    headers = {"X-CSRF-Token": csrf}
    journey = client.post(
        "/api/admin/journeys",
        headers=headers,
        json={"name": "Updated Directory", "event_date": "2026-08-17"},
    ).json()
    directory = client.get("/api/admin/recruit-directory").json()
    karl = next(item for item in directory["items"] if item["name"] == "Karl Nassar")
    added = client.post(
        f"/api/admin/journeys/{journey['id']}/recruits/from-directory",
        headers=headers,
        json={"directory_id": karl["id"]},
    ).json()
    assert added["dateOfBirth"] is None

    rows[1] = {
        **rows[1],
        "source_key": "c" * 64,
        "phone_number": "71 999999",
        "date_of_birth": date(2004, 5, 6),
        "date_of_birth_raw": "05/06/2004",
    }
    refreshed = client.post("/api/admin/recruit-directory/sync", headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    refreshed_karl = next(item for item in refreshed.json()["items"] if item["name"] == "Karl Nassar")
    assert refreshed_karl["id"] == karl["id"]
    assert refreshed_karl["dateOfBirth"] == "2004-05-06"
    updated = client.get(f"/api/admin/journeys/{journey['id']}").json()
    recruit = next(item for item in updated["recruits"] if item["id"] == added["id"])
    assert recruit["phoneNumber"] == "71 999999"
    assert recruit["dateOfBirth"] == "2004-05-06"
