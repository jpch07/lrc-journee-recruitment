from __future__ import annotations

import csv
import hashlib
import io
import re
import threading
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .assessment_runtime import active_assessment_definition
from .models import Recruit, RecruitDirectory, RecruitDirectoryState, utcnow
from .tenant import current_system_id


DIRECTORY_STATE_ID = "google_sheet"
_sync_lock = threading.Lock()


def _directory_state(db: Session) -> RecruitDirectoryState | None:
    return db.scalar(select(RecruitDirectoryState).limit(1))


def _directory_state_id() -> str:
    return current_system_id() or DIRECTORY_STATE_ID


def directory_source_url() -> str:
    participant_settings = active_assessment_definition().participants
    configured_url = participant_settings.directorySheetUrl.strip()
    sheet_name = quote(participant_settings.directorySheetName or settings.recruit_sheet_name, safe="")
    if configured_url:
        if "/gviz/tq" in configured_url or "output=csv" in configured_url:
            return configured_url
        match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", configured_url)
        if not match:
            raise RuntimeError("The configured participant Google Sheet URL is not valid.")
        return (
            f"https://docs.google.com/spreadsheets/d/{match.group(1)}/gviz/tq"
            f"?tqx=out:csv&sheet={sheet_name}"
        )
    return (
        f"https://docs.google.com/spreadsheets/d/{settings.recruit_sheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={sheet_name}"
    )


def _clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _parse_birth_date(value: str) -> date | None:
    value = _clean(value)
    if not value:
        return None
    normalized = re.sub(r"(?i)^([a-z]{3,9})(\d{1,2})\s+", r"\1 \2 ", value)
    normalized = re.sub(r"\s+", " ", normalized)
    for date_format in (
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(normalized, date_format).date()
        except ValueError:
            continue
    return None


def fetch_directory_rows() -> list[dict]:
    response = httpx.get(directory_source_url(), timeout=10, follow_redirects=True)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
    parsed: list[dict] = []
    duplicate_counts: Counter[str] = Counter()
    for source_row, raw_row in enumerate(reader, start=2):
        row = {_clean(key).casefold(): _clean(value) for key, value in raw_row.items() if key is not None}
        name = _clean(f"{row.get('first name', '')} {row.get('last name', '')}")
        if not name:
            continue
        phone = row.get("phone number", "")
        raw_birth_date = row.get("date of birth", "")
        base = "\x1f".join((name.casefold(), phone.casefold(), raw_birth_date.casefold()))
        duplicate_counts[base] += 1
        source_key = hashlib.sha256(f"{base}\x1f{duplicate_counts[base]}".encode("utf-8")).hexdigest()
        parsed.append(
            {
                "source_key": source_key,
                "source_row": source_row,
                "name": name,
                "phone_number": phone or None,
                "date_of_birth": _parse_birth_date(raw_birth_date),
                "date_of_birth_raw": raw_birth_date or None,
            }
        )
    if not parsed:
        raise RuntimeError("The List of Recruits sheet returned no named recruits.")
    return parsed


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _directory_payload(db: Session, *, stale: bool = False) -> dict:
    state = _directory_state(db)
    items = list(
        db.scalars(
            select(RecruitDirectory)
            .where(RecruitDirectory.active.is_(True))
            .order_by(func.lower(RecruitDirectory.name), RecruitDirectory.source_row)
        )
    )
    return {
        "items": [serialize_directory_recruit(item) for item in items],
        "syncedAt": state.synced_at.isoformat() if state and state.synced_at else None,
        "stale": stale,
        "lastError": state.last_error if state else "",
    }


def ensure_recruit_directory(db: Session, *, force: bool = False) -> dict:
    state = _directory_state(db)
    synced_at = _aware(state.synced_at) if state else None
    fresh = synced_at and datetime.now(timezone.utc) - synced_at < timedelta(seconds=settings.recruit_sheet_sync_seconds)
    cached = db.scalar(select(func.count()).select_from(RecruitDirectory).where(RecruitDirectory.active.is_(True))) or 0
    if not force and fresh and cached:
        return _directory_payload(db)

    with _sync_lock:
        db.expire_all()
        state = _directory_state(db)
        synced_at = _aware(state.synced_at) if state else None
        fresh = synced_at and datetime.now(timezone.utc) - synced_at < timedelta(seconds=settings.recruit_sheet_sync_seconds)
        cached = db.scalar(select(func.count()).select_from(RecruitDirectory).where(RecruitDirectory.active.is_(True))) or 0
        if not force and fresh and cached:
            return _directory_payload(db)
        try:
            rows = fetch_directory_rows()
            now = utcnow()
            existing = {
                item.source_key: item for item in db.scalars(select(RecruitDirectory))
            }
            existing_by_row_and_name = {
                (item.source_row, item.name.casefold()): item for item in existing.values()
            }
            active_existing_ids: set[str] = set()
            for values in rows:
                item = existing.get(values["source_key"])
                if item is None:
                    # Phone and birth-date edits used to change the source key.
                    # Preserve the directory identity when the same named row is
                    # updated so already-added attendance records remain linked.
                    item = existing_by_row_and_name.get(
                        (values["source_row"], values["name"].casefold())
                    )
                    if item is None:
                        item = RecruitDirectory(**values)
                        db.add(item)
                    else:
                        item.source_key = values["source_key"]
                        item.source_row = values["source_row"]
                        item.name = values["name"]
                        item.phone_number = values["phone_number"]
                        item.date_of_birth = values["date_of_birth"]
                        item.date_of_birth_raw = values["date_of_birth_raw"]
                        item.active = True
                        item.updated_at = now
                else:
                    item.source_row = values["source_row"]
                    item.name = values["name"]
                    item.phone_number = values["phone_number"]
                    item.date_of_birth = values["date_of_birth"]
                    item.date_of_birth_raw = values["date_of_birth_raw"]
                    item.active = True
                    item.updated_at = now
                item.synced_at = now
                if item.id:
                    active_existing_ids.add(item.id)
                    for recruit in db.scalars(select(Recruit).where(Recruit.directory_id == item.id)):
                        if item.phone_number and not recruit.phone_number:
                            recruit.phone_number = item.phone_number
                        if item.date_of_birth and recruit.date_of_birth is None:
                            recruit.date_of_birth = item.date_of_birth
            for item in existing.values():
                if item.id not in active_existing_ids:
                    item.active = False
                    item.updated_at = now
            if state is None:
                state = RecruitDirectoryState(
                    id=_directory_state_id(), source_url=directory_source_url(), version=1
                )
                db.add(state)
            else:
                state.version += 1
            state.source_url = directory_source_url()
            state.synced_at = now
            state.last_error = ""
            db.commit()
            return _directory_payload(db)
        except Exception as exc:
            db.rollback()
            cached = db.scalar(select(func.count()).select_from(RecruitDirectory).where(RecruitDirectory.active.is_(True))) or 0
            state = _directory_state(db)
            if state is None:
                state = RecruitDirectoryState(
                    id=_directory_state_id(), source_url=directory_source_url(), version=1
                )
                db.add(state)
            else:
                state.version += 1
            state.last_error = str(exc)[:1000]
            db.commit()
            if cached:
                return _directory_payload(db, stale=True)
            raise HTTPException(
                status_code=503,
                detail="The recruit directory is temporarily unavailable and has not been cached yet.",
            ) from exc


def serialize_directory_recruit(item: RecruitDirectory) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "phoneNumber": item.phone_number or "",
        "dateOfBirth": item.date_of_birth.isoformat() if item.date_of_birth else None,
        "dateOfBirthSource": item.date_of_birth_raw or "",
    }


def create_recruit_from_directory(db: Session, journey_id: str, directory_id: str) -> Recruit:
    entry = db.get(RecruitDirectory, directory_id)
    if not entry or not entry.active:
        raise HTTPException(status_code=404, detail="This recruit is no longer in the master recruit list.")
    duplicate = db.scalar(
        select(Recruit).where(
            Recruit.journey_id == journey_id,
            func.lower(Recruit.name) == entry.name.lower(),
        )
    )
    if duplicate:
        if duplicate.active:
            raise HTTPException(status_code=409, detail="This recruit is already in the Journee attendance.")
        duplicate.active = True
        duplicate.present = False
        duplicate.arrival_time = None
        duplicate.directory_id = entry.id
        duplicate.phone_number = entry.phone_number
        duplicate.date_of_birth = entry.date_of_birth
        duplicate.version += 1
        return duplicate
    recruit = Recruit(
        journey_id=journey_id,
        directory_id=entry.id,
        name=entry.name,
        phone_number=entry.phone_number,
        date_of_birth=entry.date_of_birth,
    )
    db.add(recruit)
    db.flush()
    return recruit
