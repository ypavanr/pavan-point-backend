from pydantic import BaseModel
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime

class LoginRequest(BaseModel):
    password: str
    role: Literal["master", "viewer", "peepee"]
    # Only meaningful (and required) when role == "viewer": a freeform display
    # name, not tied to any account - authentication is by shared password only.
    display_name: Optional[str] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class MeResponse(BaseModel):
    username: str
    role: Literal["master", "viewer", "peepee"]

class ViewerLoginLogEntry(BaseModel):
    username: str
    ip_address: Optional[str] = None
    logged_in_at: datetime

    class Config:
        from_attributes = True

class PeepeeLoginLogEntry(BaseModel):
    ip_address: Optional[str] = None
    logged_in_at: datetime

    class Config:
        from_attributes = True

class FolderBase(BaseModel):
    name: str

class FolderCreate(FolderBase):
    parent_id: Optional[str] = None
    is_private: bool = False

class FolderUpdate(BaseModel):
    name: Optional[str] = None
    is_private: Optional[bool] = None

class FolderResponse(FolderBase):
    id: str
    parent_id: Optional[str]
    is_private: bool = False
    created_at: datetime
    updated_at: datetime
    size_bytes: int = 0
    total_files: int = 0
    total_images: int = 0
    total_videos: int = 0
    total_other: int = 0
    total_folders: int = 0

    class Config:
        from_attributes = True

class FileResponse(BaseModel):
    id: str
    original_filename: str
    folder_id: Optional[str]
    file_type: str
    mime_type: str
    size_bytes: int
    has_thumbnail: bool
    capture_time: Optional[str] = "Not stored"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class NoteCreate(BaseModel):
    title: str
    folder_id: Optional[str] = None
    content_json: Dict[str, Any]

class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content_json: Optional[Dict[str, Any]] = None

class NoteResponse(BaseModel):
    id: str
    title: str
    folder_id: Optional[str]
    content_json: Dict[str, Any]
    content_plaintext: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None

    class Config:
        from_attributes = True

class NoteListItem(BaseModel):
    id: str
    title: str
    folder_id: Optional[str]
    content_plaintext: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class FolderDetailResponse(BaseModel):
    folder: Optional[FolderResponse]
    breadcrumbs: List[FolderResponse]
    subfolders: List[FolderResponse]
    files: List[FileResponse]
    notes: List[NoteListItem] = []

class FileUpdate(BaseModel):
    name: str

class DownloadZipRequest(BaseModel):
    file_ids: List[str] = []
    folder_ids: List[str] = []
    note_ids: List[str] = []

class MoveRequest(BaseModel):
    folder_id: Optional[str] = None

class StorageUsageResponse(BaseModel):
    used_bytes: int

class SearchFolderResult(FolderResponse):
    path: str

class SearchFileResult(FileResponse):
    path: str

class SearchResponse(BaseModel):
    folders: List[SearchFolderResult]
    files: List[SearchFileResult]

class TreeFolderResponse(BaseModel):
    id: str
    name: str
    is_private: bool = False
    subfolders: List['TreeFolderResponse'] = []

TreeFolderResponse.model_rebuild()

