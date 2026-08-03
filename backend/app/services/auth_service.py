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
from backend.app.services import legal_service


def authenticate(
    db: Session,
    email: str,
    password: str,
    ip: str = "",
    *,
    portal: str = "company",
) -> User:
    """
    portal=company → solo company_admin / employee (no admin de plataforma)
    portal=platform → solo role admin (acceso oculto FulfillPro)
    """
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.password_hash):
        log_security(
            db,
            title="Login fallido",
            detail=f"Intento de acceso con email {email} ({portal})",
            severity="warning",
            category="auth",
            ip=ip,
            meta={"email": email, "portal": portal},
        )
        raise HTTPException(401, "Credenciales incorrectas.")
    if not user.is_active:
        raise HTTPException(403, "Usuario desactivado.")

    if portal == "platform":
        if user.role != "admin":
            log_security(
                db,
                title="Acceso denegado a portal plataforma",
                detail=f"{email} rol={user.role}",
                severity="warning",
                category="auth",
                user_id=user.id,
                ip=ip,
            )
            raise HTTPException(403, "Acceso no autorizado a este portal.")
    else:
        if user.role == "admin":
            raise HTTPException(
                403,
                "Esta cuenta no usa el portal de empresas.",
            )
        # Normalizar legacy client → employee
        if user.role == "client":
            user.role = "employee"

    user.last_login = datetime.utcnow()
    db.commit()
    log_access(
        db,
        event_type="login",
        detail=f"Inicio de sesión ({portal})",
        user_id=user.id,
        ip=ip,
    )
    return user


def issue_tokens(db: Session, user: User) -> dict:
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
        "needs_consent": legal_service.needs_consent(db, user),
        "role": user.role,
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

    try:
        get_redis().delete(f"refresh:{user_id}:{jti}")
    except Exception:
        pass
    return issue_tokens(db, user)


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
    role = data.get("role", "employee")
    user = User(
        email=email,
        password_hash=hash_password(data["password"]),
        full_name=data.get("full_name", ""),
        role=role,
        client_code=data.get("client_code", "") or email.split("@")[0].upper(),
        company_name=data.get("company_name", ""),
        is_active=True,
        must_accept_terms=role != "admin",
        terms_accepted_at=None if role != "admin" else datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def register_with_license(db: Session, data: dict, ip: str = "") -> User:
    """
    Onboarding de empresa: el comprador/admin de empresa se registra con el código de licencia.
    Queda como company_admin. Sus empleados se crean desde el panel y firman al primer login.
    """
    from sqlalchemy.orm import joinedload

    from backend.app.models.license import License
    from backend.app.services.license_service import check_license_quotas

    email = data["email"].lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "El email ya está registrado.")

    code = (data.get("license_code") or "").upper().strip()
    lic = (
        db.query(License)
        .options(joinedload(License.devices))
        .filter(License.code == code)
        .first()
    )
    if not lic or not lic.active:
        log_security(
            db,
            title="Registro con licencia inválida",
            detail=f"Código {code} · {email}",
            severity="warning",
            category="auth",
            ip=ip,
        )
        raise HTTPException(400, "Código de licencia no válido o inactivo.")

    try:
        check_license_quotas(db, lic)
    except HTTPException as e:
        raise HTTPException(400, f"Licencia no usable: {e.detail}") from e

    # Tope de cuentas por código de licencia (antes de crear el email)
    from backend.app.services.license_service import assert_license_user_seat

    assert_license_user_seat(db, lic)

    client_code = ""
    company_name = lic.company_name or lic.label or code
    if lic.owner_user_id:
        owner = db.query(User).filter(User.id == lic.owner_user_id).first()
        if owner:
            client_code = owner.client_code or ""
            if owner.company_name:
                company_name = owner.company_name
            # Si ya hay dueño company_admin, nuevos registros públicos son empleados
            as_admin = False
        else:
            as_admin = bool(data.get("as_company_admin", True))
    else:
        as_admin = bool(data.get("as_company_admin", True))

    if not client_code:
        client_code = (lic.company_name or code).upper().replace(" ", "")[:32]

    role = "company_admin" if as_admin else "employee"

    user = User(
        email=email,
        password_hash=hash_password(data["password"]),
        full_name=data.get("full_name", ""),
        role=role,
        client_code=client_code,
        company_name=company_name,
        is_active=True,
        must_accept_terms=True,
        terms_accepted_at=None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if not lic.owner_user_id:
        lic.owner_user_id = user.id
        lic.assigned_at = datetime.utcnow()
        if not lic.company_name:
            lic.company_name = company_name
        db.commit()

    log_access(
        db,
        event_type="register",
        detail=f"Registro {role} empresa {company_name}",
        user_id=user.id,
        license_code=lic.code,
        label=lic.label,
        ip=ip,
    )
    return user


def user_out_dict(db: Session, user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "client_code": user.client_code,
        "company_name": user.company_name,
        "is_active": user.is_active,
        "must_accept_terms": bool(user.must_accept_terms),
        "terms_accepted_at": user.terms_accepted_at,
        "needs_consent": legal_service.needs_consent(db, user),
        "created_at": user.created_at,
        "last_login": user.last_login,
    }
