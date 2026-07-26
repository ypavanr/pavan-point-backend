# Drive Backend (FastAPI)

The backend handles file uploads, metadata (SQLite), thumbnail generation, and streaming video.

## Local Development (macOS)

1. Ensure you have Python 3.11 installed.
2. Ensure you have `ffmpeg` installed for video thumbnails (`brew install ffmpeg`).
3. Copy `.env.example` to `.env` and set the four credential variables plus `JWT_SECRET` (see [Role-Based Access Control](#role-based-access-control) below).
4. Run the setup script:
   ```bash
   ./run.sh
   ```
   This script will automatically create a virtual environment, install the pinned dependencies, and start the Uvicorn server on `http://127.0.0.1:8000`.
5. On startup, the app automatically migrates the SQLite schema (adds the `role` and `is_private` columns if missing - safe to run repeatedly, existing data is untouched) and seeds/updates the Master and Viewer accounts from `.env`.
6. Uploaded files are saved to the `storage/` directory, and thumbnails to the `thumbnails/` directory.

## Role-Based Access Control

There are exactly two accounts, both password-only (no username to type), seeded at startup from `.env`:

```env
MASTER_USERNAME=
MASTER_PASSWORD=
VIEWER_USERNAME=
VIEWER_PASSWORD=
JWT_SECRET=
```

`MASTER_USERNAME`/`VIEWER_USERNAME` are internal account identifiers only (used for seeding and as the master's display name) - nobody types them in. Login is by **role + password**:

- **Master** logs in with just `MASTER_PASSWORD` - it's always "you", so there's no username prompt at all.
- **Viewer** logs in with `VIEWER_PASSWORD` (one shared password for everyone you give it to) plus a freeform display name they type at login time - any name is accepted, it's not tied to an account and exists purely so you can tell who's who in the login history (see below).

`MASTER_USERNAME` and `VIEWER_USERNAME` must differ - the app refuses to start otherwise. On every startup the app also deletes any stray `users` row that isn't one of these two canonical accounts (e.g. a leftover pre-RBAC single-admin row), since login looks up a user by role alone and more than one row per role would make login nondeterministic. Passwords are hashed with bcrypt and never logged. `JWT_SECRET` signs access tokens; sessions expire after **1 hour** (`auth.ACCESS_TOKEN_EXPIRE_MINUTES`), after which the next request gets `401` and the frontend redirects to the login screen - no silent long-lived sessions.

**Master** has full control: create/rename/move/delete folders and files, upload, and toggle a folder's `is_private` flag - at creation time or any time after, via `PATCH /api/folders/{id}` with `{"is_private": true|false}`. **Viewer** is strictly read-only (browse, preview, download) and is blocked from every mutating endpoint by the `require_master` FastAPI dependency, returning `403`.

**Private folders** are hidden from Viewers everywhere - listings, search, the sidebar tree, direct-ID lookups, and zip downloads - always via `404` (never `403`), so a private folder's existence can't be inferred from the response code. Privacy is inherited by descendants: `app/utils.py::is_folder_private_or_descendant_of_private` walks the parent chain on every check rather than trusting a denormalized flag, so marking a folder private after it already has children still hides them. This function is the single source of truth used by every endpoint that needs a visibility check (`app/auth.py::check_folder_visible`).

**Viewer login history**: every successful Viewer login (the display name they typed, their IP address, and a timestamp) is written to the `viewer_login_logs` table and exposed at `GET /api/auth/viewer-logs` - Master-only (`403` for Viewers). The frontend surfaces this as the "Logs" button in the top bar (visible to Master only). The IP comes from `app/utils.py::get_client_ip`, which prefers `X-Real-IP` (set by an nginx reverse proxy in front of this app, if one is used), falls back to the first hop of `X-Forwarded-For`, and finally the raw socket peer for direct/local access. These headers are client-suppliable and unverified, so this is informational/audit logging only - never used for rate limiting or access control.

Other hardening in place: login is rate-limited (`LOGIN_RATE_LIMIT`, default `5/minute`) via `slowapi`; all IDs are UUIDs (no sequential/guessable IDs anywhere); every request is logged (display name, role, method, path, status, timestamp) to a rotating file in `logs/`; CORS is locked to `ALLOWED_ORIGINS`; `storage/`, `thumbnails/`, and `drive.db` are never mounted as static files - all access goes through authenticated API endpoints.

## Notes (Rich Text Documents)

A `Note` (`app/models.py::Note`) is a content type that lives inside a folder next to files, with the same folder/visibility semantics as `File` - it just has no bytes on disk. Columns: `id`, `folder_id` (nullable, `null` = root, same convention as `File.folder_id`), `title`, `content_json` (a Tiptap/ProseMirror document, stored as a JSON string), `content_plaintext` (regenerated server-side on every save via a cheap tree walk, used for grid/list preview snippets so the frontend never has to re-parse the JSON), `created_at`/`updated_at`, and `created_by` (the master user id, kept for consistency with the RBAC audit logging even though there's only one master).

**Why Tiptap**: the editor (bold/italic/underline/strike, text color, highlight, alignment, headings 1-3 via a paragraph-style dropdown, bullet/numbered/task lists, links) is a set of React components under `frontend/components/notes/` and `frontend/components/NoteEditorBody.jsx`, built on [Tiptap](https://tiptap.dev)/ProseMirror and configured in one place (`frontend/lib/noteEditorExtensions.js`) shared by both edit and read-only rendering. All rendering, formatting state, and editing logic run in the browser - the backend's job is limited to validating and storing/retrieving a JSON string in SQLite and regenerating the plaintext extract, the same negligible-CPU operations as any other file metadata write. There is no server-side HTML rendering or text processing of any kind, keeping notes as lightweight on the Pi as the existing file endpoints. PDF export (see below) is also entirely client-side, for the same reason.

### Endpoints

| Method | Path | Access | Notes |
|---|---|---|---|
| `POST` | `/api/notes` | Master only | Body: `{title, folder_id, content_json}`. Validates and stores `content_json`, regenerates `content_plaintext`. |
| `GET` | `/api/notes/{id}` | Master or Viewer | Returns the full note (title + `content_json`) for rendering/editing. Viewers get the same `check_folder_visible` 404-not-403 treatment as files - a note in a private-or-descendant folder can't be distinguished from a nonexistent one. |
| `PATCH` | `/api/notes/{id}` | Master only | Body: any of `{title?, content_json?}`. Regenerates `content_plaintext` if content changed, bumps `updated_at`. |
| `DELETE` | `/api/notes/{id}` | Master only | |
| `GET` | `/api/notes/{id}/export` | Master or Viewer | Streams `content_plaintext` as a `.txt` file - a plaintext fallback used by the bulk zip download below. Subject to the same visibility check. |

Notes are included in `GET /api/folders/{id}` (and the `root` listing) alongside `subfolders`/`files`, filtered by the exact same `check_folder_visible`/`is_folder_private_or_descendant_of_private` logic already used for files - there is no separate/parallel visibility check for notes. `POST /api/files/download-zip` optionally accepts `note_ids` and will also export any notes nested inside a requested folder as `.txt` files in the zip, dropping private ones for a Viewer exactly like it already does for files.

The note editor's own **Download** button does not call this endpoint - it renders a PDF client-side (`frontend/lib/notePdfExport.js`) directly from the note's `content_json` using [jsPDF](https://github.com/parallax/jsPDF), preserving bold/italic/underline/strike/color/highlight/alignment/headings/lists/links as real, selectable PDF text. An earlier version screenshotted the rendered DOM with html2canvas, but that library can't parse the `oklch()`/`lab()` CSS color functions Tailwind v4 emits and threw on every export - walking the JSON directly avoids that dependency entirely and produces a smaller, sharper, and actually-searchable PDF besides.

### Content validation (stored-XSS prevention)

Note content is user-authored rich text that gets rendered back into other users' browsers, which is a classic stored-XSS vector. `content_json` is never trusted as-is:

- **Every** create/update walks the submitted ProseMirror document (`app/notes_validation.py::validate_note_content`) against an explicit allow-list of node types (`doc`, `paragraph`, `text`, `heading`, `bulletList`, `orderedList`, `listItem`, `hardBreak`, `taskList`, `taskItem`) and mark types (`bold`, `italic`, `underline`, `strike`, `highlight`, `textStyle`, `link`). Anything else - including any raw-HTML-shaped node - is rejected with `422` and nothing is saved.
- Heading levels are restricted to 1-3, `textAlign` to `left`/`center`/`right`/`justify`, a `taskItem`'s `checked` attribute must be a boolean, and a `highlight`/`textStyle` mark's `color` attribute must be a hex color - all matching exactly what the toolbar can produce.
- A `link` mark's `href` is parsed (`urllib.parse.urlparse`) and its scheme must be `http`, `https`, or `mailto` - `javascript:`, `data:`, and every other scheme are rejected with `422`, closing off the classic link-based XSS vector before it ever reaches SQLite.
- `content_json` is capped at ~3MB (`MAX_CONTENT_JSON_BYTES`); anything larger is rejected with `422` before it touches SQLite.
- The frontend never renders untrusted content via `dangerouslySetInnerHTML` - a note is always rendered by mounting the same Tiptap editor in a non-editable (`editable: false`) state, so there's no HTML-string-to-DOM step to sanitize in the first place. Tiptap's JSON is the only format that's ever stored or transmitted; nothing converts it to raw HTML server-side.

`tests/test_notes.py` covers all of this: RBAC (`403` for a Viewer attempting create/update/delete), the private-folder visibility rule (hidden from listings, `404` on direct `GET`), a disallowed node type and a disallowed mark type each rejected with `422` and unsaved, an oversized payload rejected with `422`, `content_plaintext` regeneration after an update, task-item `checked` state, a hex text color accepted / a named CSS color rejected, and a `link` mark accepted for `https://` but rejected for `javascript:`/`data:` hrefs. It runs alongside the RBAC suite below.

### Running the security tests

```bash
source venv/bin/activate
pytest tests/ -v
```

`tests/test_access_control.py` specifically tries to break these rules (Viewer attempting mutations, reaching private folders/files by direct ID, inheritance, mixed-content zip downloads, wrong-password-for-role login, a JWT forged with the wrong secret, an expired-but-correctly-signed JWT, a stray duplicate account row breaking role lookup) plus positive-control tests confirming Master can do the equivalent actions. `tests/test_notes.py` does the same for the Notes endpoints (see above). All must pass.

## Moving to the Raspberry Pi 5

This backend is designed with zero dependencies requiring manual compilation on `aarch64` when using Python 3.11.

1. Clone or copy this repository to your Raspberry Pi.
2. Install system dependencies:
   ```bash
   sudo apt update
   sudo apt install python3.11 python3.11-venv ffmpeg libjpeg-dev caddy
   ```
   *(Note: `libjpeg-dev` is recommended for Pillow, though binary wheels usually cover it).*
3. If migrating existing data, copy the `backend/storage/`, `backend/thumbnails/`, and `backend/drive.db` files to the Pi.
4. Run the application:
   ```bash
   ./run.sh
   ```

## Public Internet Exposure (Dynamic DNS & HTTPS)

To access this securely from the public internet:

1. **Dynamic DNS**: Pick a provider (e.g., DuckDNS) and set up a cron job on your Pi to keep the IP updated.
2. **Port Forwarding**: Forward ports `80` and `443` on your home router to your Raspberry Pi's local IP address.
3. **Caddy (HTTPS)**: 
   - Open `deploy/Caddyfile` and replace `<my-ddns-domain>` with your actual domain.
   - Copy it to the Caddy config directory:
     ```bash
     sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
     sudo systemctl reload caddy
     ```
   Caddy will automatically provision a Let's Encrypt SSL certificate.
4. **Systemd Service**:
   - Edit `deploy/drive-backend.service` and ensure the `WorkingDirectory` and `ExecStart` paths point to your actual backend folder.
   - Install the service to run on boot:
     ```bash
     sudo cp deploy/drive-backend.service /etc/systemd/system/
     sudo systemctl daemon-reload
     sudo systemctl enable --now drive-backend
     ```
