from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.core.rate_limit import rate_limit_from_request
from backend.app.database import get_db
from backend.app.dependencies import get_current_user, require_admin
from backend.app.models.user import User
from backend.app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserCreate,
    UserOut,
)
from backend.app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Login portal empresas (company_admin / employee)."""
    settings = get_settings()
    rate_limit_from_request(request, "login", settings.rate_limit_login, 60)
    ip = request.client.host if request.client else ""
    user = auth_service.authenticate(db, body.email, body.password, ip=ip, portal="company")
    return auth_service.issue_tokens(db, user)


@router.post("/login/platform", response_model=TokenResponse)
def login_platform(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Login oculto del administrador de la plataforma FulfillPro."""
    settings = get_settings()
    rate_limit_from_request(request, "login_platform", settings.rate_limit_login, 60)
    ip = request.client.host if request.client else ""
    user = auth_service.authenticate(db, body.email, body.password, ip=ip, portal="platform")
    return auth_service.issue_tokens(db, user)


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Registro del admin de empresa con código de licencia."""
    settings = get_settings()
    rate_limit_from_request(request, "register", settings.rate_limit_login, 60)
    ip = request.client.host if request.client else ""
    user = auth_service.register_with_license(db, body.model_dump(), ip=ip)
    return auth_service.issue_tokens(db, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    return auth_service.refresh_tokens(db, body.refresh_token)


@router.post("/logout")
def logout(
    body: Optional[RefreshRequest] = None,
    user: User = Depends(get_current_user),
):
    auth_service.logout(user.id, body.refresh_token if body else None)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return auth_service.user_out_dict(db, user)


@router.post("/users", response_model=UserOut)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return auth_service.create_user(db, body.model_dump())
