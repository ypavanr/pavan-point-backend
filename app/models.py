import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, CheckConstraint, Text
from sqlalchemy.orm import relationship, backref
from app.database import Base

def generate_uuid():
    return str(uuid.uuid4())

def get_utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=generate_uuid)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String, nullable=False)

class ViewerLoginLog(Base):
    __tablename__ = "viewer_login_logs"
    id = Column(String, primary_key=True, default=generate_uuid)
    username = Column(String, nullable=False)
    ip_address = Column(String, nullable=True)
    logged_in_at = Column(DateTime, default=get_utcnow)

class PeepeeLoginLog(Base):
    __tablename__ = "peepee_login_logs"
    id = Column(String, primary_key=True, default=generate_uuid)
    ip_address = Column(String, nullable=True)
    logged_in_at = Column(DateTime, default=get_utcnow)

class Folder(Base):
    __tablename__ = "folders"
    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    parent_id = Column(String, ForeignKey("folders.id"), nullable=True)
    owner_role = Column(String, nullable=False, default="master", server_default="master")
    is_private = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, default=get_utcnow)
    updated_at = Column(DateTime, default=get_utcnow, onupdate=get_utcnow)

    # Relationships
    # NOTE: remote_side must live on the backref (the many-to-one "parent" side),
    # not on "subfolders" itself, or the one-to-many direction inverts.
    subfolders = relationship("Folder", cascade="all, delete", backref=backref("parent", remote_side=[id]))
    files = relationship("File", back_populates="folder", cascade="all, delete")
    notes = relationship("Note", back_populates="folder", cascade="all, delete")

class File(Base):
    __tablename__ = "files"
    id = Column(String, primary_key=True, default=generate_uuid)
    original_filename = Column(String, nullable=False)
    stored_filename = Column(String, unique=True, nullable=False)
    folder_id = Column(String, ForeignKey("folders.id"), nullable=True)
    owner_role = Column(String, nullable=False, default="master", server_default="master")
    file_type = Column(String, nullable=False) # 'image' or 'video'
    mime_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    thumbnail_path = Column(String, nullable=True)
    capture_time = Column(String, nullable=True, default="Not stored", server_default="Not stored")
    created_at = Column(DateTime, default=get_utcnow)
    updated_at = Column(DateTime, default=get_utcnow, onupdate=get_utcnow)

    folder = relationship("Folder", back_populates="files")

class Note(Base):
    __tablename__ = "notes"
    id = Column(String, primary_key=True, default=generate_uuid)
    folder_id = Column(String, ForeignKey("folders.id"), nullable=True)
    owner_role = Column(String, nullable=False, default="master", server_default="master")
    title = Column(String, nullable=False)
    content_json = Column(Text, nullable=False)  # Tiptap/ProseMirror document, stored as a JSON string
    content_plaintext = Column(Text, nullable=False, default="")  # regenerated on every save, used for grid/list previews
    created_at = Column(DateTime, default=get_utcnow)
    updated_at = Column(DateTime, default=get_utcnow, onupdate=get_utcnow)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)

    folder = relationship("Folder", back_populates="notes")
