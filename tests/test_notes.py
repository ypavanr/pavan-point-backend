"""Tests for the Notes feature: reuses the same Master/Viewer RBAC and
private-folder visibility infrastructure as files (see test_access_control.py),
plus the note-content security validation described in the Notes build spec.
"""
from tests.conftest import auth_headers


def _doc(text="Hello world"):
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]},
        ],
    }


def _create_folder(client, token, name, parent_id=None, is_private=False):
    res = client.post(
        "/api/folders",
        headers=auth_headers(token),
        json={"name": name, "parent_id": parent_id, "is_private": is_private},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _create_note(client, token, title, folder_id=None, content_json=None):
    res = client.post(
        "/api/notes",
        headers=auth_headers(token),
        json={"title": title, "folder_id": folder_id, "content_json": content_json or _doc()},
    )
    assert res.status_code == 200, res.text
    return res.json()


# 1. Master creates a note in a non-private folder -> 200, appears in folder
#    listing for both master and viewer.
def test_master_creates_note_visible_to_both_roles(client, master_token, viewer_token):
    folder_id = _create_folder(client, master_token, "Notes Public Folder")
    note = _create_note(client, master_token, "Meeting notes", folder_id)

    master_listing = client.get(f"/api/folders/{folder_id}", headers=auth_headers(master_token))
    assert master_listing.status_code == 200
    assert any(n["id"] == note["id"] for n in master_listing.json()["notes"])

    viewer_listing = client.get(f"/api/folders/{folder_id}", headers=auth_headers(viewer_token))
    assert viewer_listing.status_code == 200
    assert any(n["id"] == note["id"] for n in viewer_listing.json()["notes"])

    viewer_get = client.get(f"/api/notes/{note['id']}", headers=auth_headers(viewer_token))
    assert viewer_get.status_code == 200
    assert viewer_get.json()["content_plaintext"] == "Hello world"


# 2. Master creates a note in a private folder -> viewer's folder listing
#    excludes it; viewer's direct GET -> 404.
def test_note_in_private_folder_hidden_from_viewer(client, master_token, viewer_token):
    folder_id = _create_folder(client, master_token, "Notes Private Folder", is_private=True)
    note = _create_note(client, master_token, "Secret note", folder_id)

    viewer_listing = client.get(f"/api/folders/{folder_id}", headers=auth_headers(viewer_token))
    assert viewer_listing.status_code == 404

    viewer_get = client.get(f"/api/notes/{note['id']}", headers=auth_headers(viewer_token))
    assert viewer_get.status_code == 404

    viewer_export = client.get(f"/api/notes/{note['id']}/export", headers=auth_headers(viewer_token))
    assert viewer_export.status_code == 404

    # Master still sees it fine.
    master_get = client.get(f"/api/notes/{note['id']}", headers=auth_headers(master_token))
    assert master_get.status_code == 200


# 3. Viewer cannot create, update, or delete notes -> 403 in all three cases.
def test_viewer_cannot_create_note(client, viewer_token):
    res = client.post(
        "/api/notes",
        headers=auth_headers(viewer_token),
        json={"title": "Should not exist", "folder_id": None, "content_json": _doc()},
    )
    assert res.status_code == 403


def test_viewer_cannot_update_note(client, master_token, viewer_token):
    note = _create_note(client, master_token, "Viewer cannot edit this", None)
    res = client.patch(
        f"/api/notes/{note['id']}",
        headers=auth_headers(viewer_token),
        json={"title": "Hacked title"},
    )
    assert res.status_code == 403


def test_viewer_cannot_delete_note(client, master_token, viewer_token):
    note = _create_note(client, master_token, "Viewer cannot delete this", None)
    res = client.delete(f"/api/notes/{note['id']}", headers=auth_headers(viewer_token))
    assert res.status_code == 403


# 4. Disallowed node/mark types are rejected with 422 and nothing is saved.
def test_disallowed_node_type_rejected(client, master_token):
    bad_doc = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "hi"}]},
            {"type": "image", "attrs": {"src": "javascript:alert(1)"}},
        ],
    }
    res = client.post(
        "/api/notes",
        headers=auth_headers(master_token),
        json={"title": "Malicious note", "folder_id": None, "content_json": bad_doc},
    )
    assert res.status_code == 422

    # Confirm nothing with this title was persisted.
    listing = client.get("/api/folders/root", headers=auth_headers(master_token))
    assert not any(n["title"] == "Malicious note" for n in listing.json()["notes"])


def test_disallowed_mark_type_rejected(client, master_token):
    bad_doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "hi", "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}]}],
            }
        ],
    }
    res = client.post(
        "/api/notes",
        headers=auth_headers(master_token),
        json={"title": "Malicious mark note", "folder_id": None, "content_json": bad_doc},
    )
    assert res.status_code == 422


def test_raw_html_like_node_rejected_on_update(client, master_token):
    note = _create_note(client, master_token, "Will be attacked on update", None)
    bad_doc = {"type": "doc", "content": [{"type": "html", "content": "<script>alert(1)</script>"}]}
    res = client.patch(
        f"/api/notes/{note['id']}",
        headers=auth_headers(master_token),
        json={"content_json": bad_doc},
    )
    assert res.status_code == 422

    # Original content must be untouched.
    fetched = client.get(f"/api/notes/{note['id']}", headers=auth_headers(master_token))
    assert fetched.json()["content_plaintext"] == "Hello world"


# 5. Oversized content_json is rejected with 422.
def test_oversized_content_rejected(client, master_token):
    huge_text = "x" * (4 * 1024 * 1024)  # bigger than the 3MB ceiling
    huge_doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": huge_text}]}]}
    res = client.post(
        "/api/notes",
        headers=auth_headers(master_token),
        json={"title": "Huge note", "folder_id": None, "content_json": huge_doc},
    )
    assert res.status_code == 422


# 6. content_plaintext is regenerated after an update to content_json.
def test_plaintext_regenerated_on_update(client, master_token):
    note = _create_note(client, master_token, "Regen test", None, _doc("Original text"))
    assert note["content_plaintext"] == "Original text"

    res = client.patch(
        f"/api/notes/{note['id']}",
        headers=auth_headers(master_token),
        json={"content_json": _doc("Updated text")},
    )
    assert res.status_code == 200
    assert res.json()["content_plaintext"] == "Updated text"

    refetched = client.get(f"/api/notes/{note['id']}", headers=auth_headers(master_token))
    assert refetched.json()["content_plaintext"] == "Updated text"


# Positive control: master can create, edit, and delete a note end-to-end,
# including headings/lists/marks that mirror the toolbar's allowed formatting.
def test_master_full_note_lifecycle_with_rich_formatting(client, master_token):
    rich_doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 2, "textAlign": "center"}, "content": [{"type": "text", "text": "Title"}]},
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "bold item", "marks": [{"type": "bold"}]},
                                    {"type": "hardBreak"},
                                    {"type": "text", "text": "highlighted", "marks": [{"type": "highlight", "attrs": {"color": "#fff59d"}}]},
                                ],
                            }
                        ],
                    }
                ],
            },
        ],
    }
    note = _create_note(client, master_token, "Rich note", None, rich_doc)
    assert "Title" in note["content_plaintext"]
    assert "bold item" in note["content_plaintext"]

    rename = client.patch(f"/api/notes/{note['id']}", headers=auth_headers(master_token), json={"title": "Renamed rich note"})
    assert rename.status_code == 200
    assert rename.json()["title"] == "Renamed rich note"

    delete = client.delete(f"/api/notes/{note['id']}", headers=auth_headers(master_token))
    assert delete.status_code == 200

    gone = client.get(f"/api/notes/{note['id']}", headers=auth_headers(master_token))
    assert gone.status_code == 404


def test_note_export_returns_plaintext_as_txt(client, master_token):
    note = _create_note(client, master_token, "Export me", None, _doc("Export body text"))
    res = client.get(f"/api/notes/{note['id']}/export", headers=auth_headers(master_token))
    assert res.status_code == 200
    assert res.text == "Export body text"
    assert "Export me.txt" in res.headers["content-disposition"]


def test_download_zip_includes_note_as_txt_and_drops_private_for_viewer(client, master_token, viewer_token):
    import io
    import zipfile

    pub_note = _create_note(client, master_token, "Zip Public Note", None, _doc("public body"))
    priv_folder = _create_folder(client, master_token, "Notes Zip Private Folder", is_private=True)
    priv_note = _create_note(client, master_token, "Zip Private Note", priv_folder, _doc("private body"))

    res = client.post(
        "/api/files/download-zip",
        headers=auth_headers(viewer_token),
        json={"file_ids": [], "folder_ids": [], "note_ids": [pub_note["id"], priv_note["id"]]},
    )
    assert res.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = zf.namelist()
    assert any("Zip Public Note" in n for n in names)
    assert not any("Zip Private Note" in n for n in names)
