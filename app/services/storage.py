import os
import shutil
import zipfile
import tempfile
from pathlib import Path
from fastapi import UploadFile
from typing import List, AsyncGenerator
from sqlalchemy.orm import Session
from app.config import settings
from app import models

def save_upload_file(upload_file: UploadFile, stored_filename: str) -> Path:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    file_path = settings.storage_dir / stored_filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return file_path

def delete_file_from_disk(stored_filename: str):
    file_path = settings.storage_dir / stored_filename
    if file_path.exists():
        os.remove(file_path)

def delete_thumbnail_from_disk(thumbnail_filename: str):
    if thumbnail_filename:
        file_path = settings.thumbnails_dir / thumbnail_filename
        if file_path.exists():
            os.remove(file_path)

def delete_folder_recursive(db: Session, folder: models.Folder):
    """Recursively delete subfolders and files."""
    for subfolder in folder.subfolders:
        delete_folder_recursive(db, subfolder)
    
    for file in folder.files:
        delete_file_from_disk(file.stored_filename)
        delete_thumbnail_from_disk(file.thumbnail_path)
        db.delete(file)
    
    db.delete(folder)

async def stream_zip_files(db: Session, file_ids: List[str], folder_ids: List[str], note_ids: List[str] = None, viewer: bool = False) -> AsyncGenerator[bytes, None]:
    note_ids = note_ids or []
    files_to_zip = []
    notes_to_zip = []

    def add_folder_to_zip_list(folder_id: str, current_path: str):
        folder = db.query(models.Folder).filter(models.Folder.id == folder_id).first()
        if not folder:
            return
        # A caller may legitimately request a non-private folder that itself
        # contains a private subfolder further down - keep excluding those too.
        if viewer and folder.is_private:
            return

        new_path = os.path.join(current_path, folder.name)

        for file in folder.files:
            files_to_zip.append({
                "disk_path": settings.storage_dir / file.stored_filename,
                "zip_path": os.path.join(new_path, file.original_filename)
            })

        for note in folder.notes:
            notes_to_zip.append({
                "content": note.content_plaintext or "",
                "zip_path": os.path.join(new_path, f"{note.title}.txt")
            })

        for subfolder in folder.subfolders:
            add_folder_to_zip_list(subfolder.id, new_path)

    for fid in file_ids:
        file = db.query(models.File).filter(models.File.id == fid).first()
        if file:
            files_to_zip.append({
                "disk_path": settings.storage_dir / file.stored_filename,
                "zip_path": file.original_filename
            })

    for nid in note_ids:
        note = db.query(models.Note).filter(models.Note.id == nid).first()
        if note:
            notes_to_zip.append({
                "content": note.content_plaintext or "",
                "zip_path": f"{note.title}.txt"
            })

    for fld_id in folder_ids:
        add_folder_to_zip_list(fld_id, "")

    with tempfile.NamedTemporaryFile(delete=False) as temp_zip:
        temp_zip_path = temp_zip.name

    try:
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zipf:
            for item in files_to_zip:
                if os.path.exists(item["disk_path"]):
                    zipf.write(item["disk_path"], item["zip_path"])
            for note_item in notes_to_zip:
                zipf.writestr(note_item["zip_path"], note_item["content"])

        with open(temp_zip_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk
    finally:
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)
