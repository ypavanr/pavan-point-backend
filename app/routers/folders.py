from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app import database, models, schemas, auth, utils
from app.config import settings
from app.utils import get_folder_path, get_folder_stats, is_folder_private_or_descendant_of_private
from app.services import storage

router = APIRouter(prefix="/api/folders", tags=["Folders"])

def _is_viewer(current_user: models.User) -> bool:
    return current_user.role != "master"

def _breadcrumb_path(db: Session, parent_id: str | None, partition: str) -> str:
    """Human-readable 'My Drive / A / B' path for the folder containing an item."""
    parts = [f.name for f in get_folder_path(db, parent_id, partition)] if parent_id else []
    return " / ".join(["My Drive", *parts])

# NOTE: this must be registered before the "/{folder_id}" route below, otherwise
# a request for "/api/folders/search" would be swallowed as folder_id="search".
@router.get("/search", response_model=schemas.SearchResponse)
def search_items(q: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    q = q.strip()
    if not q:
        return schemas.SearchResponse(folders=[], files=[])

    viewer = _is_viewer(current_user)
    partition = auth.get_current_partition(current_user)

    matched_folders = db.query(models.Folder).filter(
        models.Folder.owner_role == partition,
        models.Folder.name.ilike(f"%{q}%")
    ).limit(50).all()
    if viewer:
        matched_folders = [f for f in matched_folders if not is_folder_private_or_descendant_of_private(db, f.id, partition)]

    matched_files = db.query(models.File).filter(
        models.File.owner_role == partition,
        models.File.original_filename.ilike(f"%{q}%")
    ).limit(50).all()
    if viewer:
        matched_files = [f for f in matched_files if not is_folder_private_or_descendant_of_private(db, f.folder_id, partition)]

    folder_results = [
        schemas.SearchFolderResult(**{c.name: getattr(f, c.name) for c in models.Folder.__table__.columns}, path=_breadcrumb_path(db, f.parent_id, partition), **get_folder_stats(db, f.id, partition))
        for f in matched_folders
    ]
    file_results = [
        schemas.SearchFileResult(
            **{c.name: getattr(f, c.name) for c in models.File.__table__.columns if c.name != "thumbnail_path"},
            has_thumbnail=bool(f.thumbnail_path) and (settings.thumbnails_dir / f.thumbnail_path).exists(),
            path=_breadcrumb_path(db, f.folder_id, partition),
        )
        for f in matched_files
    ]
    return schemas.SearchResponse(folders=folder_results, files=file_results)

@router.get("/tree", response_model=list[schemas.TreeFolderResponse])
def get_folder_tree(db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    partition = auth.get_current_partition(current_user)
    folders = db.query(models.Folder).filter(models.Folder.owner_role == partition).all()

    if _is_viewer(current_user):
        # A private-or-descendant folder must be entirely absent from the tree
        # (used to populate the sidebar) - not just unopenable.
        folders = [f for f in folders if not is_folder_private_or_descendant_of_private(db, f.id, partition)]

    folder_dict = {f.id: {"id": f.id, "name": f.name, "is_private": f.is_private, "subfolders": []} for f in folders}
    tree = []

    for f in folders:
        if f.parent_id and f.parent_id in folder_dict:
            folder_dict[f.parent_id]["subfolders"].append(folder_dict[f.id])
        else:
            tree.append(folder_dict[f.id])

    def sort_tree(nodes):
        nodes.sort(key=lambda x: x["name"])
        for node in nodes:
            sort_tree(node["subfolders"])

    sort_tree(tree)
    return tree

@router.post("", response_model=schemas.FolderResponse)
def create_folder(folder: schemas.FolderCreate, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    if folder.parent_id:
        parent = db.query(models.Folder).filter(models.Folder.id == folder.parent_id, models.Folder.owner_role == partition).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent folder not found")

        # Check for unique name in parent folder
        existing = db.query(models.Folder).filter(models.Folder.parent_id == folder.parent_id, models.Folder.name == folder.name, models.Folder.owner_role == partition).first()
    else:
        existing = db.query(models.Folder).filter(models.Folder.parent_id == None, models.Folder.name == folder.name, models.Folder.owner_role == partition).first()

    if existing:
        raise HTTPException(status_code=400, detail="Folder with this name already exists in this location")

    new_folder = models.Folder(name=folder.name, parent_id=folder.parent_id, is_private=folder.is_private, owner_role=partition)
    db.add(new_folder)
    db.commit()
    db.refresh(new_folder)
    return new_folder

@router.get("/{folder_id}", response_model=schemas.FolderDetailResponse)
def get_folder_contents(folder_id: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.get_current_user)):
    viewer = _is_viewer(current_user)
    partition = auth.get_current_partition(current_user)

    if folder_id == "root":
        subfolders = db.query(models.Folder).filter(models.Folder.parent_id == None, models.Folder.owner_role == partition).order_by(models.Folder.name).all()
        if viewer:
            subfolders = [f for f in subfolders if not is_folder_private_or_descendant_of_private(db, f.id, partition)]
        subfolder_dicts = [{**f.__dict__, **get_folder_stats(db, f.id, partition)} for f in subfolders]
        files = db.query(models.File).filter(models.File.folder_id == None, models.File.owner_role == partition).order_by(models.File.original_filename).all()
        notes = db.query(models.Note).filter(models.Note.folder_id == None, models.Note.owner_role == partition).order_by(models.Note.title).all()

        from datetime import datetime
        root_folder = {
            "name": "My Drive",
            "id": "root",
            "parent_id": None,
            "is_private": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            **get_folder_stats(db, None, partition)
        }

        return schemas.FolderDetailResponse(
            folder=root_folder,
            breadcrumbs=[],
            subfolders=subfolder_dicts,
            files=[{**f.__dict__, "has_thumbnail": bool(f.thumbnail_path) and (settings.thumbnails_dir / f.thumbnail_path).exists()} for f in files],
            notes=notes,
        )

    folder = db.query(models.Folder).filter(models.Folder.id == folder_id, models.Folder.owner_role == partition).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Same 404 whether the folder doesn't exist or is hidden from this viewer.
    auth.check_folder_visible(folder_id, current_user, db)

    breadcrumbs = get_folder_path(db, folder_id, partition)
    # Exclude the current folder from breadcrumbs since it's included separately
    breadcrumb_dicts = [{**b.__dict__, **get_folder_stats(db, b.id, partition)} for b in breadcrumbs[:-1]]

    subfolders = db.query(models.Folder).filter(models.Folder.parent_id == folder_id, models.Folder.owner_role == partition).order_by(models.Folder.name).all()
    if viewer:
        subfolders = [f for f in subfolders if not is_folder_private_or_descendant_of_private(db, f.id, partition)]
    subfolder_dicts = [{**f.__dict__, **get_folder_stats(db, f.id, partition)} for f in subfolders]

    files = db.query(models.File).filter(models.File.folder_id == folder_id, models.File.owner_role == partition).order_by(models.File.original_filename).all()
    notes = db.query(models.Note).filter(models.Note.folder_id == folder_id, models.Note.owner_role == partition).order_by(models.Note.title).all()

    return schemas.FolderDetailResponse(
        folder={**folder.__dict__, **get_folder_stats(db, folder.id, partition)},
        breadcrumbs=breadcrumb_dicts,
        subfolders=subfolder_dicts,
        files=[{**f.__dict__, "has_thumbnail": bool(f.thumbnail_path) and (settings.thumbnails_dir / f.thumbnail_path).exists()} for f in files],
        notes=notes,
    )

@router.patch("/{folder_id}", response_model=schemas.FolderResponse)
def update_folder(folder_id: str, folder_update: schemas.FolderUpdate, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    folder = db.query(models.Folder).filter(models.Folder.id == folder_id, models.Folder.owner_role == partition).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder_update.name is not None:
        existing = db.query(models.Folder).filter(models.Folder.parent_id == folder.parent_id, models.Folder.name == folder_update.name, models.Folder.id != folder_id, models.Folder.owner_role == partition).first()
        if existing:
            raise HTTPException(status_code=400, detail="Folder with this name already exists in this location")
        folder.name = folder_update.name

    if folder_update.is_private is not None:
        folder.is_private = folder_update.is_private

    db.commit()
    db.refresh(folder)
    return folder

@router.delete("/{folder_id}")
def delete_folder(folder_id: str, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    folder = db.query(models.Folder).filter(models.Folder.id == folder_id, models.Folder.owner_role == partition).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    storage.delete_folder_recursive(db, folder)
    db.commit()
    return {"message": "Folder deleted"}

@router.post("/{folder_id}/move", response_model=schemas.FolderResponse)
def move_folder(folder_id: str, payload: schemas.MoveRequest, db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_write_access)):
    partition = auth.get_current_partition(current_user)
    folder = db.query(models.Folder).filter(models.Folder.id == folder_id, models.Folder.owner_role == partition).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    target_id = payload.folder_id
    if target_id == folder_id:
        raise HTTPException(status_code=400, detail="Cannot move a folder into itself")

    if target_id:
        target = db.query(models.Folder).filter(models.Folder.id == target_id, models.Folder.owner_role == partition).first()
        if not target:
            raise HTTPException(status_code=404, detail="Destination folder not found")
        # Prevent moving a folder into one of its own descendants
        cursor = target
        while cursor is not None:
            if cursor.id == folder_id:
                raise HTTPException(status_code=400, detail="Cannot move a folder into its own subfolder")
            cursor = db.query(models.Folder).filter(models.Folder.id == cursor.parent_id, models.Folder.owner_role == partition).first() if cursor.parent_id else None

    existing = db.query(models.Folder).filter(
        models.Folder.parent_id == target_id,
        models.Folder.name == folder.name,
        models.Folder.id != folder_id,
        models.Folder.owner_role == partition,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="A folder with this name already exists in the destination")

    folder.parent_id = target_id
    db.commit()
    db.refresh(folder)
    return folder
