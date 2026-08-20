from __future__ import annotations

import csv
import io
import re
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .assessment_config import AssessmentSystemDefinition, blank_assessment_definition
from .assessment_service import (
    draft_definition,
    ensure_assessment_system,
    historical_impact,
    preset_definition,
    publish_definition,
    published_definition,
    save_draft,
)
from .auth import UserContext, require_csrf, require_owner
from .db import get_db
from .models import AssessmentSystemVersion, EvaluatorDirectory
from .utils import audit, loads
from .tenant import current_system_id


router = APIRouter(prefix="/api/configurator", tags=["assessment-configurator"])


class DefinitionRequest(BaseModel):
    definition: dict[str, Any]
    base_version: int = Field(ge=1)


class PublishRequest(DefinitionRequest):
    change_summary: str = Field(default="", max_length=1000)


def _google_csv_url(url: str, sheet_name: str) -> str:
    clean = url.strip()
    if "/gviz/tq" in clean or "output=csv" in clean:
        return clean
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", clean)
    if not match:
        raise HTTPException(status_code=422, detail="Enter a valid Google Sheet URL for the assessor directory.")
    return (f"https://docs.google.com/spreadsheets/d/{match.group(1)}/gviz/tq"
            f"?tqx=out:csv&sheet={quote(sheet_name or 'Evaluators', safe='')}")


def _row_value(row: dict[str, str], *aliases: str) -> str:
    normalized = {" ".join(str(key or "").casefold().split()): " ".join(str(value or "").split())
                  for key, value in row.items()}
    return next((normalized.get(alias, "") for alias in aliases if normalized.get(alias, "")), "")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _payload(db: Session) -> dict:
    system = ensure_assessment_system(db)
    published = published_definition(db, system)
    draft = draft_definition(system)
    return {
        "system": {
            "id": system.id,
            "name": system.name,
            "slug": system.slug,
            "version": system.version,
            "publishedVersion": system.published_version,
            "updatedBy": system.updated_by,
            "updatedAt": system.updated_at.isoformat(),
        },
        "draft": draft.model_dump(mode="json"),
        "published": published.model_dump(mode="json"),
        "impact": historical_impact(db, system, draft),
        # A newly created workspace already starts from the neutral definition.
        # Presets are intentionally not promoted in the owner experience.
        "presets": [],
    }


@router.get("")
def get_configuration(
    context: UserContext = Depends(require_owner), db: Session = Depends(get_db),
):
    del context
    return _payload(db)


@router.get("/public")
def public_system_configuration(db: Session = Depends(get_db)):
    if not current_system_id():
        definition = blank_assessment_definition().model_copy(update={"name": "Assessment Workspace"})
        system = None
    else:
        system = ensure_assessment_system(db)
        definition = published_definition(db, system)
    return {
        "name": definition.name,
        "terminology": definition.terminology.model_dump(mode="json"),
        "branding": definition.branding.model_dump(mode="json"),
        "features": definition.features.model_dump(mode="json"),
        "dashboard": definition.dashboard.model_dump(mode="json"),
        "participants": definition.participants.model_dump(mode="json"),
        "assessors": definition.assessors.model_dump(mode="json"),
        "activities": [{"key": item.key, "name": item.name,
                        "assignment": item.assignment.model_dump(mode="json"),
                        "scoring": item.scoring,
                        "commentsEnabled": item.commentsEnabled}
                       for item in definition.activities if item.enabled],
        "dimensions": [item.model_dump(mode="json") for item in definition.dimensions],
        "generalFactors": [item.model_dump(mode="json") for item in definition.generalFactors],
        "assessorCategories": [item.model_dump(mode="json") for item in definition.assessors.categories],
        "accessProfiles": [item.model_dump(mode="json") for item in definition.accessProfiles if item.enabled],
        "scoreMaximum": float(definition.scoring.officialMaximum),
        "publishedVersion": system.published_version if system else 0,
        "configured": bool(system),
    }


@router.post("/validate")
def validate_configuration(
    payload: DefinitionRequest,
    context: UserContext = Depends(require_owner), db: Session = Depends(get_db),
):
    del context
    definition = AssessmentSystemDefinition.model_validate(payload.definition)
    system = ensure_assessment_system(db)
    return {"valid": True, "impact": historical_impact(db, system, definition)}


@router.put("/draft")
def update_draft(
    payload: DefinitionRequest,
    request: Request,
    context: UserContext = Depends(require_owner),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    definition = AssessmentSystemDefinition.model_validate(payload.definition)
    system = ensure_assessment_system(db)
    try:
        save_draft(db, system, definition, base_version=payload.base_version, actor_name=context.username)
    except ValueError as exc:
        if str(exc) == "stale":
            raise HTTPException(status_code=409, detail="The configuration changed elsewhere. Reload before saving.") from exc
        raise
    audit(db, journey_id=None, actor_type="owner", actor_name=context.username,
          action="assessment_configuration.draft_saved", entity_type="assessment_system", entity_id=system.id,
          after={"name": definition.name, "version": system.version})
    _commit(db)
    return _payload(db)


@router.post("/preset/{preset_key}")
def load_preset(
    preset_key: str,
    payload: DefinitionRequest,
    request: Request,
    context: UserContext = Depends(require_owner),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    try:
        definition = preset_definition(preset_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown assessment-system preset.") from exc
    system = ensure_assessment_system(db)
    try:
        save_draft(db, system, definition, base_version=payload.base_version, actor_name=context.username)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="The configuration changed elsewhere. Reload before saving.") from exc
    audit(db, journey_id=None, actor_type="owner", actor_name=context.username,
          action="assessment_configuration.preset_loaded", entity_type="assessment_system", entity_id=system.id,
          after={"preset": preset_key, "version": system.version})
    _commit(db)
    return _payload(db)


@router.post("/publish")
def publish_configuration(
    payload: PublishRequest,
    request: Request,
    context: UserContext = Depends(require_owner),
    db: Session = Depends(get_db),
):
    require_csrf(request, context.csrf_token)
    definition = AssessmentSystemDefinition.model_validate(payload.definition)
    system = ensure_assessment_system(db)
    impact = historical_impact(db, system, definition)
    if impact["blocked"]:
        raise HTTPException(status_code=409, detail=impact["message"])
    try:
        record = publish_definition(
            db, system, definition, base_version=payload.base_version,
            actor_name=context.username, change_summary=payload.change_summary,
        )
    except ValueError as exc:
        if str(exc) == "stale":
            raise HTTPException(status_code=409, detail="The configuration changed elsewhere. Reload before publishing.") from exc
        if str(exc) == "historical":
            raise HTTPException(status_code=409, detail=impact["message"]) from exc
        raise
    audit(db, journey_id=None, actor_type="owner", actor_name=context.username,
          action="assessment_configuration.published", entity_type="assessment_system_version", entity_id=record.id,
          after={"version": record.version, "name": definition.name, "summary": payload.change_summary})
    _commit(db)
    return _payload(db)


@router.post("/sync-assessors")
def sync_assessor_directory(
    payload: DefinitionRequest,
    request: Request,
    context: UserContext = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Import the configured assessor Sheet into the proven master directory."""
    require_csrf(request, context.csrf_token)
    definition = AssessmentSystemDefinition.model_validate(payload.definition)
    configured = definition.assessors
    if not configured.linkedDirectoryEnabled or not configured.directorySheetUrl.strip():
        raise HTTPException(status_code=422, detail="Enable the linked assessor directory and enter its Google Sheet URL first.")
    try:
        response = httpx.get(_google_csv_url(configured.directorySheetUrl, configured.directorySheetName),
                             timeout=15, follow_redirects=True)
        response.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The assessor Google Sheet could not be read.") from exc
    category_by_value = {
        value.casefold(): category.key
        for category in configured.categories
        for value in (category.key, category.name)
    }
    fallback = sorted(configured.categories, key=lambda item: (item.secondaryPriority, item.name.casefold()))[0].key
    existing = {item.name.casefold(): item for item in db.scalars(select(EvaluatorDirectory))}
    imported: set[str] = set()
    for row in rows:
        username = _row_value(row, "nickname", "username", "evaluator", "name")
        if not username:
            continue
        role_text = _row_value(row, "role", "category", "type").casefold()
        values = {
            "default_role": category_by_value.get(role_text, fallback),
            "full_name": _row_value(row, "full name", "official name") or None,
            "phone_number": _row_value(row, "phone number", "phone", "mobile number", "mobile") or None,
        }
        key = username.casefold()
        imported.add(key)
        item = existing.get(key)
        if item is None:
            db.add(EvaluatorDirectory(name=username, active=True, **values))
        else:
            item.default_role = values["default_role"]
            item.full_name = values["full_name"]
            item.phone_number = values["phone_number"]
            item.active = True
    if not imported:
        raise HTTPException(status_code=422, detail="The configured assessor Sheet contains no recognizable usernames.")
    audit(db, journey_id=None, actor_type="owner", actor_name=context.username,
          action="assessor_directory.synced", entity_type="evaluator_directory", entity_id=None,
          after={"count": len(imported), "sheetName": configured.directorySheetName})
    _commit(db)
    return {"ok": True, "imported": len(imported)}


@router.get("/versions")
def configuration_versions(
    context: UserContext = Depends(require_owner), db: Session = Depends(get_db),
):
    del context
    system = ensure_assessment_system(db)
    records = db.scalars(select(AssessmentSystemVersion).where(
        AssessmentSystemVersion.system_id == system.id,
    ).order_by(AssessmentSystemVersion.version.desc()))
    return [{
        "id": item.id,
        "version": item.version,
        "name": loads(item.definition_json, {}).get("name", "Assessment system"),
        "changeSummary": item.change_summary,
        "publishedBy": item.published_by,
        "createdAt": item.created_at.isoformat(),
    } for item in records]
