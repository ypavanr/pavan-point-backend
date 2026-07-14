import os
from sqlalchemy.orm import Session
from app import models

def get_folder_path(db: Session, folder_id: str) -> list[models.Folder]:
    """Returns the path from root to the specified folder."""
    path = []
    current_id = folder_id
    while current_id:
        folder = db.query(models.Folder).filter(models.Folder.id == current_id).first()
        if not folder:
            break
        path.insert(0, folder)
        current_id = folder.parent_id
    return path

def is_folder_private_or_descendant_of_private(db: Session, folder_id: str | None) -> bool:
    """Single source of truth for folder visibility: walks the parent chain
    (rather than trusting a denormalized flag) so a folder marked private
    after its children already exist still hides those children."""
    current_id = folder_id
    while current_id:
        folder = db.query(models.Folder).filter(models.Folder.id == current_id).first()
        if not folder:
            return False
        if folder.is_private:
            return True
        current_id = folder.parent_id
    return False

def get_folder_stats(db: Session, folder_id: str | None) -> dict:
    """Recursively calculates aggregate stats of all files and folders in a folder."""
    stats = {"size_bytes": 0, "total_files": 0, "total_images": 0, "total_videos": 0, "total_other": 0, "total_folders": 0}
    
    # Files directly in this folder
    files = db.query(models.File).filter(models.File.folder_id == folder_id).all()
    stats["total_files"] += len(files)
    stats["size_bytes"] += sum(f.size_bytes for f in files)
    stats["total_images"] += sum(1 for f in files if f.file_type == 'image')
    stats["total_videos"] += sum(1 for f in files if f.file_type == 'video')
    stats["total_other"] += sum(1 for f in files if f.file_type == 'other')
    
    # Subfolders
    subfolders = db.query(models.Folder).filter(models.Folder.parent_id == folder_id).all()
    stats["total_folders"] += len(subfolders)
    
    for sub in subfolders:
        sub_stats = get_folder_stats(db, sub.id)
        for k in stats:
            stats[k] += sub_stats[k]
        
    return stats
