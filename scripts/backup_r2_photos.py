"""Read-only, bounded backup of the configured private photo bucket.

Credentials are taken only from LRC_R2_* environment variables. The archive is
NOT encrypted: choose a private, non-synced directory and protect it like the
database backup. No database connection or remote mutation is performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import zipfile


def inventory(client, bucket):
    rows = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            rows.append({"key": item["Key"], "size": int(item["Size"]), "etag": item["ETag"]})
    return sorted(rows, key=lambda item: item["key"])


def backup(client, bucket: str, output: Path, *, max_bytes: int = 1024**3) -> dict:
    rows = inventory(client, bucket)
    total = sum(row["size"] for row in rows)
    if total > max_bytes or any(row["size"] > 64 * 1024**2 for row in rows):
        raise ValueError("Photo backup exceeds its explicit safety limit")
    if output.exists():
        raise FileExistsError("Refusing to overwrite a backup")
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    # 'x' prevents a concurrent process from replacing an existing archive.
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_STORED) as archive:
        for index, item in enumerate(rows):
            result = client.get_object(Bucket=bucket, Key=item["key"], IfMatch=item["etag"])
            stream = result["Body"]
            try:
                raw = stream.read(item["size"] + 1)
            finally:
                stream.close()
            if len(raw) != item["size"]:
                raise ValueError("Object size changed during backup")
            digest = hashlib.sha256(raw).hexdigest()
            remote_digest = result.get("Metadata", {}).get("sha256")
            if remote_digest and remote_digest != digest:
                raise ValueError("Object checksum mismatch")
            # Object names never become local paths or ZIP traversal names.
            member = f"objects/{index:08d}.bin"
            archive.writestr(member, raw)
            records.append({**item, "member": member, "sha256": digest,
                            "content_type": result.get("ContentType", "application/octet-stream")})
        if inventory(client, bucket) != rows:
            raise ValueError("Bucket changed during backup; retry with a fresh archive")
        manifest = {"format": "evalday-r2-backup-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                    "bucket": bucket, "object_count": len(rows), "bytes": total, "objects": records}
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    # A manifest is written only after every download passed. Independently read
    # the finished archive back, so success is not merely an upload/download claim.
    with zipfile.ZipFile(output) as archive:
        stored = json.loads(archive.read("manifest.json"))
        for item in stored["objects"]:
            raw = archive.read(item["member"])
            if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("Local backup verification failed")
    return {"objects": len(rows), "bytes": total, "verified": True, "remote_writes": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-bytes", type=int, default=1024**3)
    args = parser.parse_args()
    try:
        endpoint = os.environ["LRC_R2_ENDPOINT_URL"]
        address = urlsplit(endpoint)
        if address.scheme != "https" or not (address.hostname or "").endswith(".r2.cloudflarestorage.com"):
            raise ValueError("Expected an HTTPS Cloudflare R2 endpoint")
        import boto3
        from botocore.config import Config
        client = boto3.client("s3", endpoint_url=endpoint, region_name="auto",
                              aws_access_key_id=os.environ["LRC_R2_ACCESS_KEY_ID"],
                              aws_secret_access_key=os.environ["LRC_R2_SECRET_ACCESS_KEY"],
                              config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}))
        result = backup(client, os.environ["LRC_R2_BUCKET"], args.output, max_bytes=args.max_bytes)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        # Provider exception messages may echo credentials or object identifiers.
        print(json.dumps({"verified": False, "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
