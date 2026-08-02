from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from app import database, models, schemas, auth
from app.config import settings
from app.rate_limit import limiter
from app.utils import get_client_ip

router = APIRouter(prefix="/api/auth", tags=["Auth"])

MAX_DISPLAY_NAME_LENGTH = 50

@router.post("/login", response_model=schemas.LoginResponse)
@limiter.limit(lambda: settings.login_rate_limit)
def login(request: Request, payload: schemas.LoginRequest, db: Session = Depends(database.get_db)):
    # There's exactly one account per role - Master is always "me", and Viewer
    # is a single shared password (no per-person account), so login is by
    # role + password only. Same generic 401 either way, so a wrong password
    # can't be distinguished from "that role doesn't exist".
    user = db.query(models.User).filter(models.User.role == payload.role).first()
    if not user or not auth.verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    if payload.role == "viewer":
        display_name = (payload.display_name or "").strip()[:MAX_DISPLAY_NAME_LENGTH]
        if not display_name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Please enter your name")
        db.add(models.ViewerLoginLog(username=display_name, ip_address=get_client_ip(request), logged_in_at=models.get_utcnow()))
        db.commit()
    elif payload.role == "peepee":
        ip_address = get_client_ip(request)
        if ip_address != "106.51.34.28":
            db.add(models.PeepeeLoginLog(ip_address=ip_address, logged_in_at=models.get_utcnow()))
            db.commit()
        display_name = user.username
    else:
        display_name = user.username

    access_token = auth.create_access_token(
        data={"sub": user.id, "username": display_name, "role": user.role}
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=schemas.MeResponse)
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    return {"username": current_user.display_name, "role": current_user.role}

@router.get("/viewer-logs", response_model=list[schemas.ViewerLoginLogEntry])
def get_viewer_logs(db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_master)):
    return (
        db.query(models.ViewerLoginLog)
        .order_by(models.ViewerLoginLog.logged_in_at.desc())
        .limit(500)
        .all()
    )

@router.get("/peepee-logs", response_model=list[schemas.PeepeeLoginLogEntry])
def get_peepee_logs(db: Session = Depends(database.get_db), current_user: models.User = Depends(auth.require_master)):
    return (
        db.query(models.PeepeeLoginLog)
        .order_by(models.PeepeeLoginLog.logged_in_at.desc())
        .limit(500)
        .all()
    )
