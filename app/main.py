import logging
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.config import settings
from app.database import engine, Base, SessionLocal
from app import models, auth, migrations
from app.rate_limit import limiter
from app.routers import auth_routes, folders, files, notes


app = FastAPI(title="Self-Hosted Photo/Video Drive")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"]
)

settings.logs_dir.mkdir(parents=True, exist_ok=True)
access_logger = logging.getLogger("drive.access")
access_logger.setLevel(logging.INFO)
if not access_logger.handlers:
    handler = RotatingFileHandler(settings.logs_dir / "access.log", maxBytes=5_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(message)s"))
    access_logger.addHandler(handler)

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    # Uploaded files are served back with their client-supplied Content-Type
    # (see routers/files.py); nosniff stops a browser from ever executing one
    # as HTML/script if opened directly, regardless of that declared type.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    response = await call_next(request)
    access_logger.info(
        "%s username=%s role=%s method=%s path=%s status=%s",
        datetime.now(timezone.utc).isoformat(),
        getattr(request.state, "log_username", "-"),
        getattr(request.state, "log_role", "-"),
        request.method,
        request.url.path,
        response.status_code,
    )
    return response

app.include_router(auth_routes.router)
app.include_router(folders.router)
app.include_router(files.router)
app.include_router(notes.router)

@app.on_event("startup")
def run_migrations_and_seed_users():
    migrations.run_startup_migrations(engine)
    Base.metadata.create_all(bind=engine)

    if settings.master_username == settings.viewer_username:
        raise RuntimeError("MASTER_USERNAME and VIEWER_USERNAME must not be the same value")

    db = SessionLocal()
    try:
        # Login now looks up a user by role alone (no per-account username to
        # disambiguate), so exactly one row per role must exist. Older
        # installs may still have a leftover pre-RBAC "admin" row (migrated to
        # role=master by the ALTER TABLE default) sitting alongside the
        # dedicated master/viewer rows below - remove anything that isn't one
        # of the two canonical accounts so role lookups can't pick the wrong
        # row / wrong password hash.
        canonical_usernames = {settings.master_username, settings.viewer_username}
        db.query(models.User).filter(~models.User.username.in_(canonical_usernames)).delete(synchronize_session=False)

        for username, password, role in (
            (settings.master_username, settings.master_password, "master"),
            (settings.viewer_username, settings.viewer_password, "viewer"),
        ):
            existing = db.query(models.User).filter(models.User.username == username).first()
            if existing:
                existing.role = role
                existing.hashed_password = auth.get_password_hash(password)
            else:
                db.add(models.User(username=username, hashed_password=auth.get_password_hash(password), role=role))
        db.commit()
    finally:
        db.close()

@app.get("/api/health", tags=["Health"])
def health_check():
    return {"status": "ok"}

# TEMPORARY - for verifying the ThinkPad nginx -> Pi reverse proxy forwards
# headers correctly (Browser -> nginx -> this FastAPI app). Remove once the
# proxy setup is confirmed; it has no auth and reflects request headers back.
@app.get("/", tags=["Debug"])
async def proxy_header_check(request: Request):
    return {
        "host": request.headers.get("host"),
        "real_ip": request.headers.get("x-real-ip"),
        "forwarded_for": request.headers.get("x-forwarded-for"),
        "proto": request.headers.get("x-forwarded-proto"),
        "client_ip_seen_by_fastapi": request.client.host if request.client else None,
    }
