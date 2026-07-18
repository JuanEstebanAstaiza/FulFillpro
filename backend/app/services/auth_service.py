from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    safe_decode,
    verify_password,
)
from backend.app.models.user import User
from backend.app.redis_client import cache_delete, get_redis
from backend.app.services.audit_service import log_access, log_security


def authenticate(db: Session, email: str, password: str, ip: str = "") -> User:
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.password_hash):
        log_security(
            db,
            title="Login fallido",
            detail=f"Intento de acceso con email {email}",
            severity="warning",
            category="auth",
            ip=ip,
            meta={"email": email},
        )
        raise HTTPException(401, "Credenciales incorrectas.")
    if not user.is_active:
        log_security(
            db,
            title="Login usuario inactivo",
            detail=f"Usuario {email} desactivado",
            severity="warning",
            category="auth",
            user_id=user.id,
            ip=ip,
        )
        raise HTTPException(403, "Usuario desactivado.")
    user.last_login = datetime.utcnow()
    db.commit()
    log_access(db, event_type="login", detail="Inicio de sesión", user_id=user.id, ip=ip)
    return user


def issue_tokens(user: User) -> dict:
    access = create_access_token(
        str(user.id),
        extra={"role": user.role, "email": user.email, "client_code": user.client_code},
    )
    refresh, jti = create_refresh_token(str(user.id))
    try:
        get_redis().setex(f"refresh:{user.id}:{jti}", 14 * 24 * 3600, "1")
    except Exception:
        pass
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
    }


def refresh_tokens(db: Session, refresh_token: str) -> dict:
    payload = safe_decode(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, "Refresh token inválido.")
    jti = payload.get("jti")
    user_id = payload.get("sub")
    try:
        if not get_redis().get(f"refresh:{user_id}:{jti}"):
            raise HTTPException(401, "Sesión expirada o cerrada.")
    except HTTPException:
        raise
    except Exception:
        pass

    user = db.query(User).filter(User.id == UUID(str(user_id)), User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(401, "Usuario no válido.")

    # Rotar: invalidar jti anterior
    try:
        get_redis().delete(f"refresh:{user_id}:{jti}")
    except Exception:
        pass
    return issue_tokens(user)


def logout(user_id: UUID, refresh_token: Optional[str] = None) -> None:
    if refresh_token:
        payload = safe_decode(refresh_token)
        if payload and payload.get("jti"):
            cache_delete(f"refresh:{user_id}:{payload['jti']}")
    try:
        r = get_redis()
        for key in r.scan_iter(f"refresh:{user_id}:*"):
            r.delete(key)
    except Exception:
        pass


def create_user(db: Session, data: dict) -> User:
    email = data["email"].lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "El email ya está registrado.")
    user = User(
        email=email,
        password_hash=hash_password(data["password"]),
        full_name=data.get("full_name", ""),
        role=data.get("role", "client"),
        client_code=data.get("client_code", "") or email.split("@")[0].upper(),
        company_name=data.get("company_name", ""),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
