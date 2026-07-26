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

        if "folders" in inspector.get_table_names():
            folder_columns = {c["name"] for c in inspector.get_columns("folders")}
            if "is_private" not in folder_columns:
                conn.execute(text("ALTER TABLE folders ADD COLUMN is_private BOOLEAN NOT NULL DEFAULT 0"))

        if "viewer_login_logs" in inspector.get_table_names():
            log_columns = {c["name"] for c in inspector.get_columns("viewer_login_logs")}
            if "ip_address" not in log_columns:
                conn.execute(text("ALTER TABLE viewer_login_logs ADD COLUMN ip_address VARCHAR"))
