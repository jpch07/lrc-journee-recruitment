from __future__ import annotations

from sqlalchemy import select

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

    selected = client.post(
        f"/api/platform/workspaces/{first['id']}/select",
        headers={"X-CSRF-Token": session["csrfToken"]},
    )
    assert selected.status_code == 200, selected.text
    workspace_session = client.get("/api/auth/session")
    assert workspace_session.status_code == 200
    assert workspace_session.json()["isOwner"] is True
    assert workspace_session.json()["recruitment"]["name"] == "Admissions 2027"

    selected = client.post(
        f"/api/platform/workspaces/{second['id']}/select",
        headers={"X-CSRF-Token": session["csrfToken"]},
    )
    assert selected.status_code == 200, selected.text
    assert client.get("/api/auth/session").json()["recruitment"]["name"] == "Volunteer Selection"


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
