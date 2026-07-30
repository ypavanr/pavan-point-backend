import os
import re
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, Form, File, BackgroundTasks, Request
from fastapi.responses import FileResponse as FastAPIFileResponse, StreamingResponse
from sqlalchemy.orm import Session
from app import database, models, schemas, auth
from app.config import settings
from app.services import storage, thumbnails
from app.utils import is_folder_private_or_descendant_of_private

router = APIRouter(prefix="/api/files", tags=["Files"])

def get_unique_filename(db: Session, folder_id: str | None, filename: str, partition: str, exclude_id: str | None = None) -> str:
    name, ext = os.path.splitext(filename)
    counter = 1
    new_name = filename

    while True:
        query = db.query(models.File).filter(models.File.original_filename == new_name, models.File.owner_role == partition)
        if folder_id:
            query = query.filter(models.File.folder_id == folder_id)
        else:
            query = query.filter(models.File.folder_id == None)
        if exclude_id:
            query = query.filter(models.File.id != exclude_id)

        if not query.first():
            return new_name

        new_name = f"{name} ({counter}){ext}"
        counter += 1

@router.post("/upload", response_model=schemas.FileResponse)
def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    folder_id: str = Form(None),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.require_write_access)
):
    partition = auth.get_current_partition(current_user)

    if folder_id and folder_id != "root":
        folder = db.query(models.Folder).filter(models.Folder.id == folder_id, models.Folder.owner_role == partition).first()
        if not folder:
            raise HTTPException(status_code=404, detail="Target folder not found")
        actual_folder_id = folder_id
    else:
        actual_folder_id = None

    unique_filename = get_unique_filename(db, actual_folder_id, file.filename, partition)

    import uuid
    stored_filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
    thumbnail_filename = f"thumb_{stored_filename}.jpg"

    try:
        file_path = storage.save_upload_file(file, stored_filename)
    except OSError as e:
        storage.delete_file_from_disk(stored_filename)
        raise HTTPException(status_code=507, detail=f"Failed to save file to disk (out of space?): {e}")

    size_bytes = os.path.getsize(file_path)
    if file.content_type.startswith("image/"):
        file_type = "image"
    elif file.content_type.startswith("video/"):
        file_type = "video"
    else:
        file_type = "other"

    new_file = models.File(
        original_filename=unique_filename,
        stored_filename=stored_filename,
        folder_id=actual_folder_id,
        file_type=file_type,
        mime_type=file.content_type,
        size_bytes=size_bytes,
        thumbnail_path=thumbnail_filename if file_type != "other" else None,
        owner_role=partition
    )
    db.add(new_file)
    db.commit()
    db.refresh(new_file)

    if file_type != "other":
        background_tasks.add_task(thumbnails.generate_thumbnail, file_type, stored_filename, thumbnail_filename)

    return {**new_file.__dict__, "has_thumbnail": bool(new_file.thumbnail_path) and (settings.thumbnails_dir / new_file.thumbnail_path).exists()}

def _get_visible_file_or_404(file_id: str, current_user: models.User, db: Session) -> models.File:
    """Fetch a file and, for viewers, confirm its parent folder isn't private-or-descendant.
    Always 404 (never 403) so private content can't be distinguished from missing content."""
    partition = auth.get_current_partition(current_user)
    file = db.query(models.File).filter(models.File.id == file_id, models.File.owner_role == partition).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    auth.check_folder_visible(file.folder_id, current_user, db)
    return file

@router.get("/{file_id}/download")
def download_file(file_id: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    file = _get_visible_file_or_404(file_id, current_user, db)

    file_path = settings.storage_dir / file.stored_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File content not found on disk")

    return FastAPIFileResponse(
        path=file_path,
        filename=file.original_filename,
        media_type=file.mime_type
    )

@router.get("/{file_id}/thumbnail")
def get_thumbnail(file_id: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    file = _get_visible_file_or_404(file_id, current_user, db)
    if not file.thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    thumb_path = settings.thumbnails_dir / file.thumbnail_path
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found on disk")

    return FastAPIFileResponse(path=thumb_path, media_type="image/jpeg")

def get_range_params(request: Request, file_size: int):
    range_header = request.headers.get("range")
    if not range_header:
        return 0, file_size - 1

    try:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else file_size - 1
        return start, end
    except Exception:
        return 0, file_size - 1

@router.get("/{file_id}/preview")
def preview_file(request: Request, file_id: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    file = _get_visible_file_or_404(file_id, current_user, db)

    file_path = settings.storage_dir / file.stored_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File content not found on disk")

    if file.file_type == "video":
        file_size = os.path.getsize(file_path)
        start, end = get_range_params(request, file_size)
        end = min(end, file_size - 1)
        length = end - start + 1

        def file_iterator():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk_size = min(8192, remaining)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    yield data
                    remaining -= len(data)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": file.mime_type
        }
        return StreamingResponse(file_iterator(), status_code=206, headers=headers)

    return FastAPIFileResponse(path=file_path, media_type=file.mime_type)

@router.patch("/{file_id}", response_model=schemas.FileResponse)
def rename_file(file_id: str, file_update: schemas.FileUpdate, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    file = db.query(models.File).filter(models.File.id == file_id, models.File.owner_role == partition).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    unique_name = get_unique_filename(db, file.folder_id, file_update.name, partition, exclude_id=file.id)
    file.original_filename = unique_name
    db.commit()
    db.refresh(file)
    return {**file.__dict__, "has_thumbnail": bool(file.thumbnail_path) and (settings.thumbnails_dir / file.thumbnail_path).exists()}

@router.post("/{file_id}/move", response_model=schemas.FileResponse)
def move_file(file_id: str, payload: schemas.MoveRequest, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    file = db.query(models.File).filter(models.File.id == file_id, models.File.owner_role == partition).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    target_id = payload.folder_id
    if target_id:
        folder = db.query(models.Folder).filter(models.Folder.id == target_id, models.Folder.owner_role == partition).first()
        if not folder:
            raise HTTPException(status_code=404, detail="Destination folder not found")

    unique_name = get_unique_filename(db, target_id, file.original_filename, partition, exclude_id=file.id)
    file.folder_id = target_id
    file.original_filename = unique_name
    db.commit()
    db.refresh(file)
    return {**file.__dict__, "has_thumbnail": bool(file.thumbnail_path) and (settings.thumbnails_dir / file.thumbnail_path).exists()}

@router.get("/storage-usage", response_model=schemas.StorageUsageResponse)
def get_storage_usage(db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    from sqlalchemy import func
    partition = auth.get_current_partition(current_user)
    used = db.query(func.coalesce(func.sum(models.File.size_bytes), 0)).filter(models.File.owner_role == partition).scalar()
    return schemas.StorageUsageResponse(used_bytes=int(used))

@router.delete("/{file_id}")
def delete_file(file_id: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    file = db.query(models.File).filter(models.File.id == file_id, models.File.owner_role == partition).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    storage.delete_file_from_disk(file.stored_filename)
    storage.delete_thumbnail_from_disk(file.thumbnail_path)
    db.delete(file)
    db.commit()
    return {"message": "File deleted"}

@router.post("/download-zip")
def download_zip(request: schemas.DownloadZipRequest, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    if not request.file_ids and not request.folder_ids and not request.note_ids:
        raise HTTPException(status_code=400, detail="No items selected")

    file_ids, folder_ids, note_ids = request.file_ids, request.folder_ids, request.note_ids
    partition = auth.get_current_partition(current_user)
    is_viewer = current_user.role != "master"
    if is_viewer:
        # Silently drop anything private rather than erroring the whole request.
        file_ids = [
            fid for fid in file_ids
            if (f := db.query(models.File).filter(models.File.id == fid, models.File.owner_role == partition).first())
            and not is_folder_private_or_descendant_of_private(db, f.folder_id, partition)
        ]
        folder_ids = [fid for fid in folder_ids if not is_folder_private_or_descendant_of_private(db, fid, partition)]
        note_ids = [
            nid for nid in note_ids
            if (n := db.query(models.Note).filter(models.Note.id == nid, models.Note.owner_role == partition).first())
            and not is_folder_private_or_descendant_of_private(db, n.folder_id, partition)
        ]

    return StreamingResponse(
        storage.stream_zip_files(db, file_ids, folder_ids, note_ids, partition, viewer=is_viewer),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=drive_download.zip"}
    )
