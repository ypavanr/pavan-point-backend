"""Security tests for the Master/Viewer role-based access control layer.

Every test here is trying to *break* the rules described in the RBAC build
prompt: bypass a mutating-endpoint guard, reach a private folder by direct ID,
forge a token, or otherwise get data a Viewer should never see. All of these
must be enforced server-side - the tests talk to the FastAPI app directly via
TestClient, the same way a hostile client using curl/Postman would.
"""
import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app import auth, models
from app.database import SessionLocal
from tests.conftest import auth_headers, _login


# Regression: login looks up a user by role alone (no username to
# disambiguate), so a stray extra row sharing a role - e.g. a leftover
# pre-RBAC "admin" account migrated to role=master - would make login pick an
# arbitrary row and fail with the right password. Startup seeding must keep
# exactly one row per role.
def test_exactly_one_user_row_per_role(client):
    db = SessionLocal()
    try:
        master_count = db.query(models.User).filter(models.User.role == "master").count()
        viewer_count = db.query(models.User).filter(models.User.role == "viewer").count()
        assert master_count == 1
        assert viewer_count == 1
    finally:
        db.close()


def _upload_file(client, token, filename, folder_id=None):
    data = {}
    if folder_id:
        data["folder_id"] = folder_id
    res = client.post(
        "/api/files/upload",
        headers=auth_headers(token),
        files={"file": (filename, io.BytesIO(b"not-a-real-image-but-thats-fine"), "image/jpeg")},
        data=data,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _create_folder(client, token, name, parent_id=None, is_private=False):
    res = client.post(
        "/api/folders",
        headers=auth_headers(token),
        json={"name": name, "parent_id": parent_id, "is_private": is_private},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


@pytest.fixture(scope="module")
def structure(client, master_token):
    """Builds, as master, a folder tree with private and public branches that
    every test in this module can probe as a viewer."""
    pub_folder = _create_folder(client, master_token, "Public Folder")
    pub_file = _upload_file(client, master_token, "public.jpg", pub_folder)

    priv_folder = _create_folder(client, master_token, "Private Folder", is_private=True)
    priv_direct_file = _upload_file(client, master_token, "topsecret.jpg", priv_folder)
    priv_child_folder = _create_folder(client, master_token, "Private Child", parent_id=priv_folder)
    priv_child_file = _upload_file(client, master_token, "hidden.jpg", priv_child_folder)

    mix_folder = _create_folder(client, master_token, "Mixed Folder")
    mix_priv_sub = _create_folder(client, master_token, "Mixed Private Sub", parent_id=mix_folder, is_private=True)
    mix_pub_sub = _create_folder(client, master_token, "Mixed Public Sub", parent_id=mix_folder)

    return {
        "pub_folder": pub_folder,
        "pub_file": pub_file,
        "priv_folder": priv_folder,
        "priv_direct_file": priv_direct_file,
        "priv_child_folder": priv_child_folder,
        "priv_child_file": priv_child_file,
        "mix_folder": mix_folder,
        "mix_priv_sub": mix_priv_sub,
        "mix_pub_sub": mix_pub_sub,
    }


# 1. Viewer cannot create a folder.
def test_viewer_cannot_create_folder(client, viewer_token):
    res = client.post(
        "/api/folders",
        headers=auth_headers(viewer_token),
        json={"name": "Should Not Exist", "parent_id": None},
    )
    assert res.status_code == 403


# 2. Viewer cannot delete or rename an existing file/folder.
def test_viewer_cannot_delete_folder(client, viewer_token, structure):
    res = client.delete(f"/api/folders/{structure['pub_folder']}", headers=auth_headers(viewer_token))
    assert res.status_code == 403


def test_viewer_cannot_rename_folder(client, viewer_token, structure):
    res = client.patch(
        f"/api/folders/{structure['pub_folder']}",
        headers=auth_headers(viewer_token),
        json={"name": "Renamed"},
    )
    assert res.status_code == 403


def test_viewer_cannot_rename_file(client, viewer_token, structure):
    res = client.patch(
        f"/api/files/{structure['pub_file']}",
        headers=auth_headers(viewer_token),
        json={"name": "renamed.jpg"},
    )
    assert res.status_code == 403


def test_viewer_cannot_delete_file(client, viewer_token, structure):
    res = client.delete(f"/api/files/{structure['pub_file']}", headers=auth_headers(viewer_token))
    assert res.status_code == 403


# 3. Viewer requesting the listing of a folder marked private (real ID) -> 404.
def test_viewer_cannot_list_private_folder(client, viewer_token, structure):
    res = client.get(f"/api/folders/{structure['priv_folder']}", headers=auth_headers(viewer_token))
    assert res.status_code == 404


# 4. Viewer requesting download/preview/thumbnail of a file inside a private
#    folder, by direct file ID -> 404 for all three surfaces.
def test_viewer_cannot_download_file_in_private_folder(client, viewer_token, structure):
    res = client.get(f"/api/files/{structure['priv_direct_file']}/download", headers=auth_headers(viewer_token))
    assert res.status_code == 404


def test_viewer_cannot_preview_file_in_private_folder(client, viewer_token, structure):
    res = client.get(f"/api/files/{structure['priv_direct_file']}/preview", headers=auth_headers(viewer_token))
    assert res.status_code == 404


def test_viewer_cannot_thumbnail_file_in_private_folder(client, viewer_token, structure):
    res = client.get(f"/api/files/{structure['priv_direct_file']}/thumbnail", headers=auth_headers(viewer_token))
    assert res.status_code == 404


# 5. Inheritance: a non-private child folder nested under a private parent
#    must also be inaccessible, and so must a file living directly inside it.
def test_viewer_cannot_list_private_descendant_folder(client, viewer_token, structure):
    res = client.get(f"/api/folders/{structure['priv_child_folder']}", headers=auth_headers(viewer_token))
    assert res.status_code == 404


def test_viewer_cannot_access_file_in_private_descendant_folder(client, viewer_token, structure):
    res = client.get(f"/api/files/{structure['priv_child_file']}/download", headers=auth_headers(viewer_token))
    assert res.status_code == 404


# 6. A folder listing containing one private and one non-private subfolder
#    returns 200, with the private one silently absent (not greyed out - gone).
def test_viewer_listing_hides_private_subfolder_but_returns_200(client, viewer_token, structure):
    res = client.get(f"/api/folders/{structure['mix_folder']}", headers=auth_headers(viewer_token))
    assert res.status_code == 200
    subfolder_ids = {f["id"] for f in res.json()["subfolders"]}
    assert structure["mix_pub_sub"] in subfolder_ids
    assert structure["mix_priv_sub"] not in subfolder_ids


# 7. Logging in with the viewer's password but claiming role "master" must fail
#    (there's no username to check anymore - the viewer password simply isn't
#    valid for the master account).
def test_viewer_password_with_master_role_rejected(client):
    from tests.conftest import VIEWER_PASSWORD
    res = client.post(
        "/api/auth/login",
        json={"password": VIEWER_PASSWORD, "role": "master"},
    )
    assert res.status_code == 401


# A viewer must supply some display name; an empty one is rejected outright.
def test_viewer_login_without_display_name_rejected(client):
    from tests.conftest import VIEWER_PASSWORD
    res = client.post("/api/auth/login", json={"password": VIEWER_PASSWORD, "role": "viewer"})
    assert res.status_code == 422

    res2 = client.post("/api/auth/login", json={"password": VIEWER_PASSWORD, "role": "viewer", "display_name": "   "})
    assert res2.status_code == 422


# Any display name is accepted for viewer - authentication is password-only.
def test_viewer_login_accepts_any_display_name(client):
    from tests.conftest import VIEWER_PASSWORD
    for name in ("Alice", "Bob The Builder", "friend-42"):
        token = _login(client, VIEWER_PASSWORD, "viewer", display_name=name)
        me = client.get("/api/auth/me", headers=auth_headers(token))
        assert me.status_code == 200
        assert me.json() == {"username": name, "role": "viewer"}


# Every successful viewer login is recorded (name + timestamp) and that log is
# visible to master but off-limits to viewers themselves.
def test_viewer_login_is_logged_and_only_master_can_read_it(client, master_token):
    from tests.conftest import VIEWER_PASSWORD
    viewer_token = _login(client, VIEWER_PASSWORD, "viewer", display_name="Audit Test Viewer")

    forbidden = client.get("/api/auth/viewer-logs", headers=auth_headers(viewer_token))
    assert forbidden.status_code == 403

    res = client.get("/api/auth/viewer-logs", headers=auth_headers(master_token))
    assert res.status_code == 200
    entries = res.json()
    assert any(e["username"] == "Audit Test Viewer" for e in entries)


# 8. A JWT whose payload was edited to say role "master", re-signed with a
#    guessed/wrong secret, must fail signature verification -> 401.
def test_tampered_jwt_rejected(client, viewer_token):
    real_payload = jwt.decode(viewer_token, auth.settings.jwt_secret, algorithms=[auth.ALGORITHM])
    forged_payload = {**real_payload, "role": "master"}
    forged_token = jwt.encode(forged_payload, "attacker-guessed-secret", algorithm=auth.ALGORITHM)

    res = client.get("/api/auth/me", headers=auth_headers(forged_token))
    assert res.status_code == 401

    # Also confirm the forged token can't be used to reach a master-only endpoint.
    res2 = client.post(
        "/api/folders",
        headers=auth_headers(forged_token),
        json={"name": "Should Not Exist Either", "parent_id": None},
    )
    assert res2.status_code == 401


# A session must not outlive its 1-hour expiry - a token whose "exp" claim is
# already in the past, even though correctly signed, must be rejected.
def test_expired_jwt_rejected(client, viewer_token):
    payload = jwt.decode(viewer_token, auth.settings.jwt_secret, algorithms=[auth.ALGORITHM])
    payload["exp"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    expired_token = jwt.encode(payload, auth.settings.jwt_secret, algorithm=auth.ALGORITHM)

    res = client.get("/api/auth/me", headers=auth_headers(expired_token))
    assert res.status_code == 401


def test_access_token_expiry_is_one_hour():
    assert auth.ACCESS_TOKEN_EXPIRE_MINUTES == 60


# 9. download-zip with a mix of allowed and private-folder file IDs -> 200,
#    zip contains only the allowed files.
def test_viewer_download_zip_drops_private_files(client, viewer_token, structure):
    res = client.post(
        "/api/files/download-zip",
        headers=auth_headers(viewer_token),
        json={"file_ids": [structure["pub_file"], structure["priv_direct_file"]], "folder_ids": []},
    )
    assert res.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = zf.namelist()
    assert any("public" in n for n in names)
    assert not any("topsecret" in n for n in names)


def test_viewer_download_zip_drops_private_folder_contents(client, viewer_token, structure):
    res = client.post(
        "/api/files/download-zip",
        headers=auth_headers(viewer_token),
        json={"file_ids": [], "folder_ids": [structure["mix_folder"]]},
    )
    assert res.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = zf.namelist()
    # mix_folder itself is public and passes the top-level filter, but its
    # private subfolder must still be excluded when the zip is built.
    assert not any("Mixed Private Sub" in n for n in names)


# 10. Positive control: master can do all of the above equivalent actions.
def test_master_can_create_folder(client, master_token):
    res = client.post(
        "/api/folders",
        headers=auth_headers(master_token),
        json={"name": "Master Created Folder", "parent_id": None},
    )
    assert res.status_code == 200


def test_master_can_rename_and_delete_folder(client, master_token):
    folder_id = _create_folder(client, master_token, "Temp Folder For Master")
    rename_res = client.patch(
        f"/api/folders/{folder_id}",
        headers=auth_headers(master_token),
        json={"name": "Renamed By Master"},
    )
    assert rename_res.status_code == 200
    delete_res = client.delete(f"/api/folders/{folder_id}", headers=auth_headers(master_token))
    assert delete_res.status_code == 200


def test_master_can_list_and_download_private_content(client, master_token, structure):
    list_res = client.get(f"/api/folders/{structure['priv_folder']}", headers=auth_headers(master_token))
    assert list_res.status_code == 200

    download_res = client.get(f"/api/files/{structure['priv_direct_file']}/download", headers=auth_headers(master_token))
    assert download_res.status_code == 200


def test_master_login_with_correct_role_succeeds(client):
    from tests.conftest import MASTER_PASSWORD
    res = client.post(
        "/api/auth/login",
        json={"password": MASTER_PASSWORD, "role": "master"},
    )
    assert res.status_code == 200
    assert "access_token" in res.json()


# A folder created as non-private can be marked private later (not just at
# creation time), and that change takes effect immediately for viewers.
def test_master_can_mark_existing_folder_private_after_creation(client, master_token, viewer_token):
    folder_id = _create_folder(client, master_token, "Initially Public Folder")

    visible = client.get(f"/api/folders/{folder_id}", headers=auth_headers(viewer_token))
    assert visible.status_code == 200

    patch_res = client.patch(
        f"/api/folders/{folder_id}",
        headers=auth_headers(master_token),
        json={"is_private": True},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["is_private"] is True

    now_hidden = client.get(f"/api/folders/{folder_id}", headers=auth_headers(viewer_token))
    assert now_hidden.status_code == 404


def test_master_download_zip_includes_private_content(client, master_token, structure):
    res = client.post(
        "/api/files/download-zip",
        headers=auth_headers(master_token),
        json={"file_ids": [structure["pub_file"], structure["priv_direct_file"]], "folder_ids": []},
    )
    assert res.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = zf.namelist()
    assert any("public" in n for n in names)
    assert any("topsecret" in n for n in names)
