import os
import sys
import json
import shutil
import logging
from collections import defaultdict

# Add current directory to path to allow importing app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.models import Folder, File, Note
from app.config import settings

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def get_virtual_paths(db):
    """
    Returns a dictionary mapping:
    folder_id -> full virtual path (e.g., 'master/Photos/Vacation')
    """
    folders = db.query(Folder).all()
    folder_dict = {f.id: f for f in folders}
    
    paths = {}
    
    def resolve_path(folder_id):
        if folder_id in paths:
            return paths[folder_id]
        
        folder = folder_dict.get(folder_id)
        if not folder:
            return ""
            
        if folder.parent_id:
            parent_path = resolve_path(folder.parent_id)
            path = os.path.join(parent_path, folder.name)
        else:
            path = os.path.join(folder.owner_role, folder.name)
            
        paths[folder_id] = path
        return path

    for f in folders:
        resolve_path(f.id)
        
    # Add root paths for None
    paths[None] = ""
    return paths

def get_desired_files(db, virtual_paths):
    """
    Returns a dictionary mapping:
    entity_id (UUID string) -> desired absolute path on pendrive
    
    Also handles duplicate filenames by appending a counter.
    """
    desired_files = {}
    
    # Track used paths to handle duplicates
    used_paths = set()
    
    def get_unique_path(base_path):
        if base_path not in used_paths:
            used_paths.add(base_path)
            return base_path
            
        name, ext = os.path.splitext(base_path)
        counter = 1
        while True:
            new_path = f"{name} ({counter}){ext}"
            if new_path not in used_paths:
                used_paths.add(new_path)
                return new_path
            counter += 1

    # 1. Process standard Files
    files = db.query(File).all()
    for f in files:
        if f.folder_id:
            dir_path = virtual_paths.get(f.folder_id, f.owner_role)
        else:
            dir_path = f.owner_role
            
        desired_rel_path = os.path.join(dir_path, f.original_filename)
        unique_rel_path = get_unique_path(desired_rel_path)
        
        # Format: (type, ssd_stored_name)
        desired_files[f.id] = {
            'type': 'file',
            'rel_path': unique_rel_path,
            'stored_filename': f.stored_filename
        }
        
    # 2. Process Notes (Export as .txt)
    notes = db.query(Note).all()
    for n in notes:
        if n.folder_id:
            dir_path = virtual_paths.get(n.folder_id, n.owner_role)
        else:
            dir_path = n.owner_role
            
        # Ensure title has .txt extension
        title = n.title if n.title.endswith('.txt') else f"{n.title}.txt"
        desired_rel_path = os.path.join(dir_path, title)
        unique_rel_path = get_unique_path(desired_rel_path)
        
        desired_files[n.id] = {
            'type': 'note',
            'rel_path': unique_rel_path,
            'content': n.content_plaintext
        }

    return desired_files

def cleanup_empty_dirs(path):
    """Recursively removes empty directories."""
    if not os.path.isdir(path):
        return
    for d in os.listdir(path):
        d_path = os.path.join(path, d)
        if os.path.isdir(d_path):
            cleanup_empty_dirs(d_path)
    try:
        os.rmdir(path)
    except OSError:
        pass

def main():
    if len(sys.argv) < 2:
        print("Usage: python export_backup.py /path/to/pendrive")
        sys.exit(1)
        
    target_dir = os.path.abspath(sys.argv[1])
    if not os.path.exists(target_dir):
        logging.error(f"Target directory {target_dir} does not exist!")
        sys.exit(1)
        
    sync_state_path = os.path.join(target_dir, '.sync_state.json')
    
    # Load current state from Pendrive
    current_state = {}
    if os.path.exists(sync_state_path):
        try:
            with open(sync_state_path, 'r') as f:
                current_state = json.load(f)
        except Exception as e:
            logging.warning(f"Could not read .sync_state.json: {e}. Starting fresh.")
            current_state = {}

    db = SessionLocal()
    try:
        logging.info("Calculating virtual folder structures...")
        virtual_paths = get_virtual_paths(db)
        
        logging.info("Calculating desired file tree...")
        desired_files = get_desired_files(db, virtual_paths)
        
        operations_stats = {'deleted': 0, 'moved': 0, 'copied': 0, 'notes_written': 0}
        new_state = {}
        
        # 1. DELETIONS
        for entity_id, rel_path in current_state.items():
            if entity_id not in desired_files:
                abs_path = os.path.join(target_dir, rel_path)
                if os.path.exists(abs_path):
                    logging.info(f"Deleting removed file: {rel_path}")
                    try:
                        os.remove(abs_path)
                        operations_stats['deleted'] += 1
                    except OSError as e:
                        logging.error(f"Failed to delete {abs_path}: {e}")

        # 2. MOVES and COPIES
        for entity_id, info in desired_files.items():
            rel_path = info['rel_path']
            abs_path = os.path.join(target_dir, rel_path)
            
            # Record in new state
            new_state[entity_id] = rel_path
            
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            
            if entity_id in current_state:
                # File already exists on pendrive, check if it was moved
                old_rel_path = current_state[entity_id]
                if old_rel_path != rel_path:
                    old_abs_path = os.path.join(target_dir, old_rel_path)
                    if os.path.exists(old_abs_path):
                        logging.info(f"Moving file: {old_rel_path} -> {rel_path}")
                        try:
                            os.rename(old_abs_path, abs_path)
                            operations_stats['moved'] += 1
                        except OSError as e:
                            logging.error(f"Failed to move {old_abs_path}: {e}")
                    else:
                        # Old path is missing, treat as new copy
                        current_state.pop(entity_id, None) 
                else:
                    # File is exactly where it should be
                    pass
            
            # If not in current state (or was missing during move), we need to write/copy it
            if entity_id not in current_state:
                if info['type'] == 'file':
                    ssd_path = os.path.join(settings.storage_dir, info['stored_filename'])
                    if os.path.exists(ssd_path):
                        logging.info(f"Copying new file: {rel_path}")
                        try:
                            shutil.copy2(ssd_path, abs_path)
                            operations_stats['copied'] += 1
                        except Exception as e:
                            logging.error(f"Failed to copy {ssd_path} to {abs_path}: {e}")
                    else:
                        logging.warning(f"Source file missing on SSD: {ssd_path}")
                        
                elif info['type'] == 'note':
                    logging.info(f"Writing note: {rel_path}")
                    try:
                        with open(abs_path, 'w', encoding='utf-8') as f:
                            f.write(info['content'])
                        operations_stats['notes_written'] += 1
                    except Exception as e:
                        logging.error(f"Failed to write note {abs_path}: {e}")

        # 3. Save new state
        logging.info("Saving sync state...")
        with open(sync_state_path, 'w') as f:
            json.dump(new_state, f, indent=2)
            
        # 4. Cleanup empty directories
        logging.info("Cleaning up empty directories...")
        cleanup_empty_dirs(target_dir)
        
        logging.info("--- Sync Complete ---")
        logging.info(f"Deleted: {operations_stats['deleted']}")
        logging.info(f"Moved/Renamed: {operations_stats['moved']}")
        logging.info(f"Files Copied: {operations_stats['copied']}")
        logging.info(f"Notes Written: {operations_stats['notes_written']}")
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
