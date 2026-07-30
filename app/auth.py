from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app import models, utils

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # 1 hour - session must be re-authenticated after this
VALID_ROLES = ("master", "viewer", "peepee")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)

def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    token = request.headers.get("Authorization")
    if token and token.startswith("Bearer "):
        token = token.split(" ")[1]
    else:
        token = request.query_params.get("token")

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )
    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError:
        raise credentials_exception

    user_id = payload.get("sub")
    role = payload.get("role")
    if not user_id or role not in VALID_ROLES:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None or user.role not in VALID_ROLES:
        raise credentials_exception

    # Master and Viewer are each backed by a single shared DB row (there's no
    # per-person account), so the person's actual name - typed in at login for
    # a Viewer, fixed for Master - lives only in the JWT, not in user.username.
    # Not a mapped column; just a plain attribute for this request's use.
    user.display_name = payload.get("username") or user.username

    # Authorization always uses the current DB role, not the JWT claim, so a
    # role change takes effect immediately rather than waiting for token expiry.
    # Stored as plain strings (not the ORM object) since the DB session backing
    # `user` is closed by the time the access-log middleware runs after it.
    request.state.log_username = user.display_name
    request.state.log_role = user.role
    return user

def require_master(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.role != "master":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Master access required")
    return current_user

def require_write_access(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.role not in ("master", "peepee"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Write access required")
    return current_user

def get_current_partition(current_user: models.User) -> str:
    """Determine which partition the user is accessing."""
    if current_user.role in ("master", "viewer"):
        return "master"
    return current_user.role

def check_folder_visible(folder_id: str | None, current_user: models.User, db: Session):
    """Raise 404 (never 403) for a viewer trying to reach a private-or-descendant
    folder, so a private folder ID can't be distinguished from a nonexistent one."""
    if current_user.role in ("master", "peepee"):
        return
    if not folder_id or folder_id == "root":
        return
    if utils.is_folder_private_or_descendant_of_private(db, folder_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
