"""Avisos de plataforma: lectura para usuarios y gestión en Ops."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.dependencies import get_current_user, require_admin
from backend.app.models.user import User
from backend.app.services import announcement_service
from backend.app.services.audit_service import log_access

router = APIRouter(prefix="/api/announcements", tags=["announcements"])


class AnnouncementCreate(BaseModel):
    title: str = Field(default="Aviso de plataforma", max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    severity: str = "info"  # info | warn | danger | ok
    # Minutos hasta auto-desactivar (opcional). None = hasta desactivar a mano
    ttl_minutes: Optional[int] = Field(default=None, ge=1, le=10080)


@router.get("/active")
def active_announcements(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Avisos activos para mostrar como toast a todos los usuarios logueados."""
    return {"items": announcement_service.list_active(db)}


@router.get("")
def list_announcements(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return {"items": announcement_service.list_all(db)}


@router.post("")
def create_announcement(
    body: AnnouncementCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = announcement_service.create_announcement(
        db,
        title=body.title,
        message=body.message,
        severity=body.severity,
        created_by_email=admin.email,
        ttl_minutes=body.ttl_minutes,
    )
    log_access(
        db,
        event_type="admin",
        detail=f"Aviso publicado: {row['title'][:80]}",
        user_id=admin.id,
    )
    return row


@router.post("/{announcement_id}/deactivate")
def deactivate_announcement(
    announcement_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = announcement_service.deactivate(db, announcement_id)
    log_access(
        db,
        event_type="admin",
        detail=f"Aviso desactivado {announcement_id}",
        user_id=admin.id,
    )
    return row


@router.post("/deactivate-all")
def deactivate_all(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    n = announcement_service.deactivate_all(db)
    log_access(
        db,
        event_type="admin",
        detail=f"Avisos desactivados en masa: {n}",
        user_id=admin.id,
    )
    return {"ok": True, "deactivated": n}
