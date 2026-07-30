import json
import re
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app import database, models, schemas, auth
from app.notes_validation import validate_note_content, extract_plaintext, NoteContentValidationError, MAX_CONTENT_JSON_BYTES

router = APIRouter(prefix="/api/notes", tags=["Notes"])

def _serialize_note(note: models.Note) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "folder_id": note.folder_id,
        "content_json": json.loads(note.content_json),
        "content_plaintext": note.content_plaintext,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
        "created_by": note.created_by,
    }

def _validate_and_serialize_content(content_json: dict) -> tuple[str, str]:
    """Raises HTTPException(422) if content_json is oversized or doesn't
    conform to the allowed node/mark schema. Returns (raw_json_string, plaintext)."""
    raw = json.dumps(content_json)
    if len(raw.encode("utf-8")) > MAX_CONTENT_JSON_BYTES:
        raise HTTPException(status_code=422, detail="Note content is too large")
    try:
        validate_note_content(content_json)
    except NoteContentValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return raw, extract_plaintext(content_json)

def _get_visible_note_or_404(note_id: str, current_user: models.User, db: Session) -> models.Note:
    """Same 404-not-403 visibility rule as files: a note in a private-or-descendant
    folder can't be distinguished from one that doesn't exist."""
    partition = auth.get_current_partition(current_user)
    note = db.query(models.Note).filter(models.Note.id == note_id, models.Note.owner_role == partition).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    auth.check_folder_visible(note.folder_id, current_user, db)
    return note

@router.post("", response_model=schemas.NoteResponse)
def create_note(payload: schemas.NoteCreate, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    folder_id = payload.folder_id if payload.folder_id and payload.folder_id != "root" else None
    if folder_id:
        folder = db.query(models.Folder).filter(models.Folder.id == folder_id, models.Folder.owner_role == partition).first()
        if not folder:
            raise HTTPException(status_code=404, detail="Target folder not found")

    raw_json, plaintext = _validate_and_serialize_content(payload.content_json)

    note = models.Note(
        title=payload.title,
        folder_id=folder_id,
        content_json=raw_json,
        content_plaintext=plaintext,
        created_by=current_user.id,
        owner_role=partition,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _serialize_note(note)

@router.get("/{note_id}", response_model=schemas.NoteResponse)
def get_note(note_id: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    note = _get_visible_note_or_404(note_id, current_user, db)
    return _serialize_note(note)

@router.patch("/{note_id}", response_model=schemas.NoteResponse)
def update_note(note_id: str, payload: schemas.NoteUpdate, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    note = db.query(models.Note).filter(models.Note.id == note_id, models.Note.owner_role == partition).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    if payload.title is not None:
        note.title = payload.title
    if payload.content_json is not None:
        raw_json, plaintext = _validate_and_serialize_content(payload.content_json)
        note.content_json = raw_json
        note.content_plaintext = plaintext

    db.commit()
    db.refresh(note)
    return _serialize_note(note)

@router.delete("/{note_id}")
def delete_note(note_id: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    note = db.query(models.Note).filter(models.Note.id == note_id, models.Note.owner_role == partition).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete(note)
    db.commit()
    return {"message": "Note deleted"}

@router.get("/{note_id}/export")
def export_note(note_id: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    note = _get_visible_note_or_404(note_id, current_user, db)
    content = note.content_plaintext or ""

    def iterator():
        yield content.encode("utf-8")

    # Strip characters that could otherwise break out of the quoted filename
    # in the Content-Disposition header.
    safe_title = re.sub(r'[\r\n"]', "", note.title).strip() or "note"
    return StreamingResponse(
        iterator(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.txt"'},
    )
