"""Copy legacy inline recruit photos to R2 and verify every checksum.

Run after configuring LRC_DATABASE_URL and all four LRC_R2_* variables. Inline
bytes are retained by default so the migration can be validated safely. Pass
--remove-inline only after a database backup and a successful verification run.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import undefer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal
from app.models import Recruit
from app.object_storage import enabled, get_photo, photo_key, put_photo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove-inline", action="store_true")
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()
    if not enabled():
        raise SystemExit("R2 is not configured. Set LRC_R2_ENDPOINT_URL, ACCESS_KEY_ID, SECRET_ACCESS_KEY, and BUCKET.")

    migrated = verified = 0
    with SessionLocal() as db:
        recruits = list(db.scalars(select(Recruit).options(undefer(Recruit.photo_data)).where(
            Recruit.photo_data.is_not(None)
        )))
        for recruit in recruits:
            data = recruit.photo_data
            expected = hashlib.sha256(data).hexdigest()
            key = recruit.photo_object_key or photo_key("workspace", recruit.journey_id, recruit.id)
            stored = put_photo(key, data, recruit.photo_type or "image/webp")
            downloaded = get_photo(key)
            if downloaded is None or hashlib.sha256(downloaded).hexdigest() != expected:
                db.rollback()
                raise RuntimeError(f"R2 verification failed for recruit {recruit.id}")
            recruit.photo_object_key = stored.key
            recruit.photo_sha256 = expected
            recruit.photo_size = len(data)
            if args.remove_inline:
                recruit.photo_data = None
            migrated += 1
            verified += 1
            if migrated % max(args.batch_size, 1) == 0:
                db.commit()
        db.commit()
    print(f"Migrated and verified {verified} photo(s). Inline bytes {'removed' if args.remove_inline else 'retained'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
