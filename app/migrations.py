from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

def run_startup_migrations(engine: Engine):
    """Idempotent, additive ALTER TABLEs for columns introduced after the initial
    schema (role-based access control). Base.metadata.create_all only creates
    missing tables, so pre-existing SQLite files need these added explicitly."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        if "users" in inspector.get_table_names():
            user_columns = {c["name"] for c in inspector.get_columns("users")}
            if "role" not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR NOT NULL DEFAULT 'master'"))

            # Fix existing CheckConstraint by recreating the users table without it,
            # since SQLite doesn't support dropping constraints easily.
            # We can check if the table creation SQL has the old constraint.
            create_sql = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")).scalar()
            if create_sql and "ck_users_role" in create_sql:
                conn.execute(text("CREATE TABLE users_new (id VARCHAR NOT NULL, username VARCHAR, hashed_password VARCHAR, role VARCHAR NOT NULL DEFAULT 'master', PRIMARY KEY (id))"))
                conn.execute(text("INSERT INTO users_new SELECT id, username, hashed_password, role FROM users"))
                conn.execute(text("DROP TABLE users"))
                conn.execute(text("ALTER TABLE users_new RENAME TO users"))
                conn.execute(text("CREATE UNIQUE INDEX ix_users_username ON users (username)"))

        if "folders" in inspector.get_table_names():
            folder_columns = {c["name"] for c in inspector.get_columns("folders")}
            if "is_private" not in folder_columns:
                conn.execute(text("ALTER TABLE folders ADD COLUMN is_private BOOLEAN NOT NULL DEFAULT 0"))

        if "viewer_login_logs" in inspector.get_table_names():
            log_columns = {c["name"] for c in inspector.get_columns("viewer_login_logs")}
            if "ip_address" not in log_columns:
                conn.execute(text("ALTER TABLE viewer_login_logs ADD COLUMN ip_address VARCHAR"))

        if "folders" in inspector.get_table_names():
            folder_columns = {c["name"] for c in inspector.get_columns("folders")}
            if "owner_role" not in folder_columns:
                conn.execute(text("ALTER TABLE folders ADD COLUMN owner_role VARCHAR NOT NULL DEFAULT 'master'"))

        if "files" in inspector.get_table_names():
            file_columns = {c["name"] for c in inspector.get_columns("files")}
            if "owner_role" not in file_columns:
                conn.execute(text("ALTER TABLE files ADD COLUMN owner_role VARCHAR NOT NULL DEFAULT 'master'"))
            if "capture_time" not in file_columns:
                conn.execute(text("ALTER TABLE files ADD COLUMN capture_time VARCHAR DEFAULT 'Not stored'"))

        if "notes" in inspector.get_table_names():
            note_columns = {c["name"] for c in inspector.get_columns("notes")}
            if "owner_role" not in note_columns:
                conn.execute(text("ALTER TABLE notes ADD COLUMN owner_role VARCHAR NOT NULL DEFAULT 'master'"))
