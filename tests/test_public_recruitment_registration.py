from __future__ import annotations

from sqlalchemy import select

from app.assessment_config import blank_assessment_definition, neutralize_legacy_blank_branding
from app.db import SessionLocal
from app.models import AssessmentSystem, PlatformAccount, UserAccount


def _register(client, username: str = "Workspace Owner", password: str = "owner-pass-123") -> dict:
    response = client.post("/api/platform/register", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _create_workspace(client, csrf_token: str, name: str) -> dict:
    response = client.post(
        "/api/platform/workspaces",
        json={"name": name},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_public_signup_creates_only_a_global_owner_account(client):
    session = _register(client)
    assert session["username"] == "Workspace Owner"
    assert client.get("/api/platform/workspaces").json() == []

    with SessionLocal() as db:
        platform = db.scalar(
            select(PlatformAccount)
            .where(PlatformAccount.username == "Workspace Owner")
            .execution_options(bypass_recruitment_scope=True)
        )
        assert platform is not None
        linked = list(db.scalars(
            select(AssessmentSystem)
            .where(AssessmentSystem.owner_platform_account_id == platform.id)
            .execution_options(bypass_recruitment_scope=True)
        ))
        assert linked == []


def test_one_owner_can_create_list_and_open_multiple_workspaces(client):
    session = _register(client)
    first = _create_workspace(client, session["csrfToken"], "Admissions 2027")
    second = _create_workspace(client, session["csrfToken"], "Volunteer Selection")

    listed = client.get("/api/platform/workspaces")
    assert listed.status_code == 200
    assert {item["name"] for item in listed.json()} == {"Admissions 2027", "Volunteer Selection"}
    assert {item["slug"] for item in listed.json()} == {"admissions-2027", "volunteer-selection"}

    selected = client.post(
        f"/api/platform/workspaces/{first['id']}/select",
        headers={"X-CSRF-Token": session["csrfToken"]},
    )
    assert selected.status_code == 200, selected.text
    workspace_session = client.get("/api/auth/session")
    assert workspace_session.status_code == 200
    assert workspace_session.json()["isOwner"] is True
    assert workspace_session.json()["recruitment"]["name"] == "Admissions 2027"
    assert selected.json()["slug"] == "admissions-2027"
    assert client.get("/admissions-2027/admin").status_code == 200
    assert client.get("/admissions-2027/evaluate").status_code == 200
    assert client.get("/admissions-2027/view").status_code == 200
    assert client.get("/admissions-2027/configure").status_code == 200

    public_config = client.get("/api/configurator/public").json()
    assert public_config["branding"]["primaryColor"] == "#4f46e5"
    assert public_config["branding"]["shortMark"] == "AS"
    assert public_config["branding"]["organizationName"] == ""
    configurator = client.get("/api/configurator").json()
    assert configurator["presets"] == []

    selected = client.post(
        f"/api/platform/workspaces/{second['id']}/select",
        headers={"X-CSRF-Token": session["csrfToken"]},
    )
    assert selected.status_code == 200, selected.text
    assert client.get("/api/auth/session").json()["recruitment"]["name"] == "Volunteer Selection"


def test_workspace_names_are_unique_and_owner_can_delete_workspace(client):
    session = _register(client)
    created = _create_workspace(client, session["csrfToken"], "Admissions 2028")
    selected = client.post(
        f"/api/platform/workspaces/{created['id']}/select",
        headers={"X-CSRF-Token": session["csrfToken"]},
    ).json()
    journey = client.post(
        "/api/admin/journeys",
        json={"name": "Deletion cascade", "event_date": "2028-02-01"},
        headers={"X-CSRF-Token": selected["workspaceCsrfToken"]},
    )
    assert journey.status_code == 200, journey.text
    duplicate = client.post(
        "/api/platform/workspaces",
        json={"name": "  admissions   2028  "},
        headers={"X-CSRF-Token": session["csrfToken"]},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "A workspace with this name already exists."

    wrong_confirmation = client.request(
        "DELETE",
        f"/api/platform/workspaces/{created['id']}",
        json={"confirmation_name": "Wrong name"},
        headers={"X-CSRF-Token": session["csrfToken"]},
    )
    assert wrong_confirmation.status_code == 422
    assert len(client.get("/api/platform/workspaces").json()) == 1

    deleted = client.request(
        "DELETE",
        f"/api/platform/workspaces/{created['id']}",
        json={"confirmation_name": "Admissions 2028"},
        headers={"X-CSRF-Token": session["csrfToken"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"]["slug"] == "admissions-2028"
    assert client.get("/api/platform/workspaces").json() == []
    with SessionLocal() as db:
        assert db.scalar(
            select(AssessmentSystem.id)
            .where(AssessmentSystem.id == created["id"])
            .execution_options(bypass_recruitment_scope=True)
        ) is None


def test_configurator_cannot_rename_workspace_to_an_existing_name(client):
    session = _register(client)
    first = _create_workspace(client, session["csrfToken"], "Existing Workspace")
    second = _create_workspace(client, session["csrfToken"], "Workspace To Rename")
    selected = client.post(
        f"/api/platform/workspaces/{second['id']}/select",
        headers={"X-CSRF-Token": session["csrfToken"]},
    ).json()
    payload = client.get("/api/configurator").json()
    payload["draft"]["name"] = first["name"].lower()
    response = client.put(
        "/api/configurator/draft",
        json={"definition": payload["draft"], "base_version": payload["system"]["version"]},
        headers={"X-CSRF-Token": selected["workspaceCsrfToken"]},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "A workspace with this name already exists."


def test_workspace_owners_are_isolated(client):
    first_session = _register(client, "First Owner", "first-owner-pass")
    private_workspace = _create_workspace(client, first_session["csrfToken"], "Private Recruitment")
    client.post(
        "/api/platform/logout",
        headers={"X-CSRF-Token": first_session["csrfToken"]},
    )

    second_session = _register(client, "Second Owner", "second-owner-pass")
    assert client.get("/api/platform/workspaces").json() == []
    denied = client.post(
        f"/api/platform/workspaces/{private_workspace['id']}/select",
        headers={"X-CSRF-Token": second_session["csrfToken"]},
    )
    assert denied.status_code == 404


def test_existing_lrc_owner_is_linked_to_the_platform_account(client):
    login = client.post("/api/platform/login", json={"username": "JP Chaaya", "password": "test-password"})
    assert login.status_code == 200, login.text
    workspaces = client.get("/api/platform/workspaces").json()
    assert len(workspaces) == 1
    assert workspaces[0]["name"] == "LRC Journee Recruitment 2026"

    with SessionLocal() as db:
        owner = db.scalar(
            select(UserAccount)
            .where(UserAccount.is_owner.is_(True))
            .execution_options(bypass_recruitment_scope=True)
        )
        assert owner.platform_account_id is not None


def test_homepage_is_a_simple_neutral_login_and_signup(client):
    html = client.get("/").text
    assert "Assessment Workspace" in html
    assert "Log in" in html
    assert "Sign up" in html
    assert "Recruitment ID" not in html
    assert "Lebanese Red Cross" not in html
    assert "LRC" not in html


def test_legacy_unbranded_workspace_red_is_rendered_neutrally():
    definition = blank_assessment_definition()
    definition.branding.primaryColor = "#b20d2d"
    definition.branding.darkColor = "#192331"

    normalized = neutralize_legacy_blank_branding(definition)
    assert normalized.branding.primaryColor == "#4f46e5"
    assert normalized.branding.darkColor == "#172033"

    definition.branding.organizationName = "Lebanese Red Cross"
    branded = neutralize_legacy_blank_branding(definition)
    assert branded.branding.primaryColor == "#b20d2d"
