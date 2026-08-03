"""Endpoints para el administrador de la empresa contratante."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.security import hash_password
from backend.app.database import get_db
from backend.app.dependencies import get_current_user, require_consent
from backend.app.models.user import User
from backend.app.schemas.auth import UserOut
from backend.app.services.audit_service import log_access
from backend.app.services import license_service

router = APIRouter(prefix="/api/company", tags=["company"])


def require_company_admin(user: User = Depends(require_consent)) -> User:
    if user.role not in ("company_admin", "admin"):
        raise HTTPException(403, "Solo el administrador de la empresa puede gestionar usuarios.")
    return user


class EmployeeCreate(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=12)
    full_name: str = ""


@router.get("/employees", response_model=list[UserOut])
def list_employees(
    db: Session = Depends(get_db),
    admin: User = Depends(require_company_admin),
):
    q = db.query(User).filter(User.role.in_(["employee", "company_admin", "client"]))
    if admin.role != "admin":
        if not admin.client_code:
            return []
        q = q.filter(User.client_code == admin.client_code)
    return q.order_by(User.created_at.desc()).all()


@router.post("/employees", response_model=UserOut)
def create_employee(
    body: EmployeeCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_company_admin),
):
    """
    Crea un colaborador de la empresa. En el primer login deberá
    firmar digitalmente los términos legales vigentes.
    """
    email = body.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Ese email ya está registrado.")

    if admin.role != "admin" and not admin.client_code:
        raise HTTPException(400, "Tu cuenta no tiene código de empresa asignado.")

    client_code = admin.client_code if admin.role != "admin" else (admin.client_code or "DEMO")
    company_name = admin.company_name or ""

    # Tope de usuarios de la licencia de la empresa
    lic = license_service.get_user_license(db, admin)
    if lic:
        license_service.assert_license_user_seat(db, lic)

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.full_name.strip(),
        role="employee",
        client_code=client_code,
        company_name=company_name,
        is_active=True,
        must_accept_terms=True,
        terms_accepted_at=None,
        created_by_id=admin.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    ip = request.client.host if request.client else ""
    log_access(
        db,
        event_type="create_employee",
        detail=f"Usuario creado {email} (debe firmar términos)",
        user_id=admin.id,
        ip=ip,
    )
    return user


@router.post("/employees/{user_id}/toggle")
def toggle_employee(
    user_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_company_admin),
):
    """Activa o desactiva (bloquea). Un usuario bloqueado se puede reactivar con el mismo botón."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Usuario no encontrado.")
    if admin.role != "admin" and user.client_code != admin.client_code:
        raise HTTPException(403, "No pertenece a tu empresa.")
    if user.id == admin.id:
        raise HTTPException(400, "No puedes desactivarte a ti mismo.")
    # Al reactivar, respetar tope de asientos
    if not user.is_active:
        lic = license_service.get_user_license(db, admin)
        if lic:
            max_users = int(getattr(lic, "max_users", 0) or 0)
            if max_users > 0:
                active = (
                    db.query(User)
                    .filter(
                        User.client_code == admin.client_code,
                        User.is_active.is_(True),
                        User.role.in_(["employee", "company_admin", "client"]),
                    )
                    .count()
                )
                if active >= max_users:
                    raise HTTPException(
                        403,
                        f"No puedes reactivar: el plan permite {max_users} cuenta(s) activas.",
                    )
    user.is_active = not user.is_active
    db.commit()
    return UserOut.model_validate(user)


@router.delete("/employees/{user_id}")
def delete_employee(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_company_admin),
):
    """Elimina permanentemente un colaborador de la empresa (libera cupo de licencia)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Usuario no encontrado.")
    if admin.role != "admin" and user.client_code != admin.client_code:
        raise HTTPException(403, "No pertenece a tu empresa.")
    if user.id == admin.id:
        raise HTTPException(400, "No puedes eliminarte a ti mismo.")
    if user.role == "company_admin" and admin.role != "admin":
        # Solo platform admin borra a otro company_admin
        raise HTTPException(403, "No puedes eliminar al administrador de la empresa.")
    email = user.email
    db.delete(user)
    db.commit()
    ip = request.client.host if request.client else ""
    log_access(
        db,
        event_type="delete_employee",
        detail=f"Usuario eliminado {email}",
        user_id=admin.id,
        ip=ip,
    )
    return {"ok": True, "email": email}


@router.get("/overview")
def company_overview(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role not in ("company_admin", "admin", "employee", "client"):
        raise HTTPException(403, "Sin acceso.")
    lic = license_service.get_user_license(db, user)
    employees = 0
    if user.client_code:
        employees = (
            db.query(User)
            .filter(User.client_code == user.client_code, User.is_active.is_(True))
            .count()
        )
    return {
        "company_name": user.company_name,
        "client_code": user.client_code,
        "role": user.role,
        "employees": employees,
        "license": license_service.usage_summary(db, lic) if lic else None,
    }
