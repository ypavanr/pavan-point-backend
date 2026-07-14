import os
import tempfile
from pathlib import Path

import pytest

# Env vars must be set before `app.config.settings` is instantiated for the
# first time (module import), so the test process never touches the real
# drive.db / storage / thumbnails used by the running dev server.
_tmp_dir = Path(tempfile.mkdtemp(prefix="drive-test-"))
os.environ["MASTER_USERNAME"] = "test_master"
os.environ["MASTER_PASSWORD"] = "master-pass-123"
os.environ["VIEWER_USERNAME"] = "test_viewer"
os.environ["VIEWER_PASSWORD"] = "viewer-pass-123"
os.environ["JWT_SECRET"] = "test-jwt-secret-do-not-use-in-prod"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
os.environ["LOGIN_RATE_LIMIT"] = "1000/minute"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir / 'test_drive.db'}"
os.environ["STORAGE_DIR"] = str(_tmp_dir / "storage")
os.environ["THUMBNAILS_DIR"] = str(_tmp_dir / "thumbnails")
os.environ["LOGS_DIR"] = str(_tmp_dir / "logs")

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

MASTER_USERNAME = settings.master_username
MASTER_PASSWORD = settings.master_password
VIEWER_USERNAME = settings.viewer_username
VIEWER_PASSWORD = settings.viewer_password


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, password, role, display_name=None):
    payload = {"password": password, "role": role}
    if display_name is not None:
        payload["display_name"] = display_name
    res = client.post("/api/auth/login", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


@pytest.fixture(scope="session")
def master_token(client):
    return _login(client, MASTER_PASSWORD, "master")


@pytest.fixture(scope="session")
def viewer_token(client):
    return _login(client, VIEWER_PASSWORD, "viewer", display_name="Test Viewer")


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}
