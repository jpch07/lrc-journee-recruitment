from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.assessment_config import AssessmentSystemDefinition, blank_assessment_definition, load_stored_definition
from app.db import SessionLocal
from app.models import AssessmentSystem, AssessmentSystemVersion
from app.utils import dumps


def legacy_definition():
    raw = blank_assessment_definition().model_dump(mode="json")
    raw["schemaVersion"] = 1
    activity = deepcopy(raw["activities"][0])
    activity["key"] = "second_activity"
    activity["name"] = "Second activity"
    raw["activities"].append(activity)
    return raw


def test_legacy_weights_are_read_without_changing_scores_but_new_writes_are_strict():
    raw = legacy_definition()
    original = deepcopy(raw)
    loaded = load_stored_definition(raw)
    assert [a.criteria[0].weight for a in loaded.activities] == [1, 1]
    assert raw == original
    without_version = {key: value for key, value in raw.items() if key != "schemaVersion"}
    assert len(load_stored_definition(without_version).activities) == 2
    with pytest.raises(ValidationError, match="200%"):
        AssessmentSystemDefinition.model_validate(raw)
    with pytest.raises(ValidationError, match="200%"):
        load_stored_definition({**raw, "schemaVersion": 2})


def create_workspaces(client):
    login = client.post("/api/platform/register", json={
        "username": "Workspace Owner", "password": "workspace-test-password",
    })
    assert login.status_code == 200, login.text
    headers = {"X-CSRF-Token": login.json()["csrfToken"]}
    workspaces = []
    for name in ("Real workspace", "Test workspace"):
        response = client.post("/api/platform/workspaces", headers=headers, json={"name": name})
        assert response.status_code == 200, response.text
        workspaces.append(response.json())
    return headers, workspaces


def replace_configuration(workspace, raw):
    value = dumps(raw)
    with SessionLocal() as db:
        system = db.get(AssessmentSystem, workspace["id"])
        record = db.scalar(select(AssessmentSystemVersion).where(
            AssessmentSystemVersion.system_id == system.id,
            AssessmentSystemVersion.version == system.published_version,
        ).execution_options(bypass_recruitment_scope=True))
        record.definition_json = value
        system.draft_json = value
        db.commit()
    return value


def test_open_legacy_workspace_and_switch_back_in_same_browser(client):
    headers, (real, test) = create_workspaces(client)
    raw = legacy_definition()
    stored = replace_configuration(test, raw)
    selected = client.post(f'/api/platform/workspaces/{test["id"]}/select', headers=headers)
    assert selected.status_code == 200, selected.text
    assert client.get(f'/{test["slug"]}/admin').status_code == 200
    assert client.get("/api/admin/journeys").status_code == 200
    configuration = client.get("/api/configurator")
    assert configuration.status_code == 200, configuration.text
    assert len(configuration.json()["published"]["activities"]) == 2
    rejected = client.post("/api/configurator/validate", json={
        "definition": raw, "base_version": configuration.json()["system"]["version"],
    })
    assert rejected.status_code == 422
    assert "200%" in rejected.json()["detail"]
    assert client.get("/").status_code == 200
    assert client.get("/static/styles.css").headers["content-type"].startswith("text/css")
    assert len(client.get("/api/platform/workspaces").json()) == 2
    assert client.post(f'/api/platform/workspaces/{real["id"]}/select', headers=headers).status_code == 200
    assert client.get(f'/{real["slug"]}/admin').status_code == 200
    assert len(client.get("/api/configurator/public").json()["activities"]) == 1
    with SessionLocal() as db:
        system = db.get(AssessmentSystem, test["id"])
        assert system.draft_json == stored


@pytest.mark.parametrize("malformed", ["empty_activities", "non_object"])
def test_bad_workspace_cannot_poison_platform_assets_health_or_switching(client, malformed):
    headers, (real, test) = create_workspaces(client)
    raw = blank_assessment_definition().model_dump(mode="json")
    raw["activities"] = []
    if malformed == "non_object":
        raw = []
    replace_configuration(test, raw)
    assert client.post(f'/api/platform/workspaces/{test["id"]}/select', headers=headers).status_code == 200
    error = client.get(f'/{test["slug"]}/admin')
    assert error.status_code == 409
    assert 'href="/"' in error.text
    assert client.get("/api/admin/journeys").json()["code"] == "workspace_configuration_invalid"
    assert client.get("/").status_code == 200
    assert client.get("/static/admin.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/api/platform/session").status_code == 200
    assert len(client.get("/api/platform/workspaces").json()) == 2
    assert client.post(f'/api/platform/workspaces/{real["id"]}/select', headers=headers).status_code == 200
    assert client.get(f'/{real["slug"]}/admin').status_code == 200
    assert client.get("/api/admin/journeys").status_code == 200
    assert client.post("/api/platform/logout", headers=headers).status_code == 200


def test_home_and_static_assets_do_not_query_workspace_database(client, monkeypatch):
    def unavailable():
        raise AssertionError("Shared page must not look up a workspace")
    monkeypatch.setattr("app.main.SessionLocal", unavailable)
    for path in ("/", "/static/styles.css", "/static/admin.js", "/health/live"):
        assert client.get(path).status_code == 200
