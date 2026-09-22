import hashlib
import io
import json
import zipfile

import pytest

from scripts.backup_r2_photos import backup


@pytest.fixture(autouse=True)
def clean_database():
    """Pure archive tests must not reset the application's shared test database."""
    yield


class Bucket:
    def __init__(self, *, bad_hash=False, changed=False):
        self.body = b"synthetic-image-only"
        self.bad_hash = bad_hash
        self.changed = changed
        self.list_count = 0

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        assert kwargs == {"Bucket": "test-photos"}
        self.list_count += 1
        yield {"Contents": [{"Key": "../../not-a-local-path", "Size": len(self.body),
                              "ETag": "changed" if self.changed and self.list_count > 1 else "test-etag"}]}

    def get_object(self, **kwargs):
        assert kwargs == {"Bucket": "test-photos", "Key": "../../not-a-local-path", "IfMatch": "test-etag"}
        return {"Body": io.BytesIO(self.body), "ContentType": "image/webp",
                "Metadata": {"sha256": "wrong" if self.bad_hash else hashlib.sha256(self.body).hexdigest()}}


def test_photo_archive_verifies_and_never_uses_remote_key_as_path(tmp_path):
    output = tmp_path / "photos.zip"
    assert backup(Bucket(), "test-photos", output)["verified"]
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"objects/00000000.bin", "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["objects"][0]["key"] == "../../not-a-local-path"
    with pytest.raises(FileExistsError):
        backup(Bucket(), "test-photos", output)


@pytest.mark.parametrize("options", [{"bad_hash": True}, {"changed": True}])
def test_failed_photo_backup_has_no_complete_manifest(tmp_path, options):
    output = tmp_path / "photos.zip"
    with pytest.raises(ValueError):
        backup(Bucket(**options), "test-photos", output)
    with zipfile.ZipFile(output) as archive:
        assert "manifest.json" not in archive.namelist()


def test_photo_backup_budget_rejected_before_download(tmp_path):
    output = tmp_path / "photos.zip"
    with pytest.raises(ValueError):
        backup(Bucket(), "test-photos", output, max_bytes=1)
    assert not output.exists()
