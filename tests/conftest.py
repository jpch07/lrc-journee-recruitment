from __future__ import annotations

import os
import sys
from pathlib import Path


TEST_DB = Path(__file__).with_name("journee-test.db")
sys.path.insert(0, str(Path(__file__).parents[1]))
os.environ["LRC_DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["LRC_JOURNEE_ADMIN_PASSWORD"] = "test-password"
os.environ["LRC_JOURNEE_SESSION_SECRET"] = "test-session-secret-at-least-thirty-two-characters"
os.environ["LRC_JOURNEE_COOKIE_SECURE"] = "false"
os.environ["LRC_JOURNEE_TEST_TOOLS"] = "true"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value
