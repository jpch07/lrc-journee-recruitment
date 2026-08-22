from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .config import settings


@dataclass(frozen=True)
class StoredPhoto:
    key: str
    sha256: str
    size: int
    content_type: str


def enabled() -> bool:
    return bool(
        settings.r2_endpoint_url
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket
    )


def _client():
    if not enabled():
        return None
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def photo_key(system_id: str, journey_id: str, recruit_id: str, digest: str | None = None) -> str:
    suffix = f"-{digest[:16]}" if digest else ""
    return f"workspaces/{system_id}/journeys/{journey_id}/recruits/{recruit_id}{suffix}.webp"


def put_photo(key: str, data: bytes, content_type: str = "image/webp") -> StoredPhoto:
    digest = hashlib.sha256(data).hexdigest()
    client = _client()
    if client is not None:
        client.put_object(
            Bucket=settings.r2_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="private, max-age=86400",
            Metadata={"sha256": digest},
        )
    return StoredPhoto(key=key, sha256=digest, size=len(data), content_type=content_type)


def get_photo(key: str) -> bytes | None:
    client = _client()
    if client is None:
        return None
    try:
        return client.get_object(Bucket=settings.r2_bucket, Key=key)["Body"].read()
    except Exception as exc:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", ""))
        if code in {"NoSuchKey", "404", "NotFound"}:
            return None
        raise


def delete_photo(key: str) -> None:
    client = _client()
    if client is not None:
        client.delete_object(Bucket=settings.r2_bucket, Key=key)


def has_photo(recruit) -> bool:
    # Accessing photo_data is intentionally avoided if an object key exists.
    return bool(recruit.photo_object_key or recruit.photo_size)


def read_recruit_photo(recruit) -> bytes | None:
    if recruit.photo_object_key:
        value = get_photo(recruit.photo_object_key)
        if value is not None:
            return value
    return recruit.photo_data


def write_recruit_photo(recruit, data: bytes, content_type: str = "image/webp") -> None:
    from .models import utcnow

    if enabled():
        # Journey and recruit UUIDs are globally unique; the workspace segment
        # is informational rather than part of access control.
        digest = hashlib.sha256(data).hexdigest()
        stored = put_photo(photo_key("workspace", recruit.journey_id, recruit.id, digest), data, content_type)
        recruit.photo_object_key = stored.key
        recruit.photo_sha256 = stored.sha256
        recruit.photo_size = stored.size
        recruit.photo_data = None
    else:
        # Local development and pre-cutover databases retain the compatibility
        # fallback while still benefiting from deferred ORM loading.
        recruit.photo_data = data
        recruit.photo_object_key = None
        recruit.photo_sha256 = hashlib.sha256(data).hexdigest()
        recruit.photo_size = len(data)
    recruit.photo_type = content_type
    recruit.photo_updated_at = utcnow()


def clear_recruit_photo(recruit) -> None:
    # Clear the authoritative database reference transactionally. The object is
    # intentionally left as an inaccessible orphan until periodic cleanup; deleting
    # it before the database commit could lose the photo if that commit failed.
    recruit.photo_object_key = None
    recruit.photo_sha256 = None
    recruit.photo_size = None
    recruit.photo_data = None
    recruit.photo_type = None
    from .models import utcnow
    recruit.photo_updated_at = utcnow()
