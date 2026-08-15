"""Avisos globales publicados desde Ops."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app.models.announcement import PlatformAnnouncement
from backend.app.redis_client import cache_delete, cache_get, cache_set

CACHE_KEY = "fulfillpro:announcements:active"


def _to_dict(a: PlatformAnnouncement) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "title": a.title,
        "message": a.message,
        "severity": a.severity or "info",
        "active": bool(a.active),
        "created_by_email": a.created_by_email or "",
        "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
        "expires_at": a.expires_at.isoformat() + "Z" if a.expires_at else None,
        "deactivated_at": a.deactivated_at.isoformat() + "Z" if a.deactivated_at else None,
    }


def _expire_stale(db: Session) -> None:
    now = datetime.utcnow()
    stale = (
        db.query(PlatformAnnouncement)
        .filter(
            PlatformAnnouncement.active.is_(True),
            PlatformAnnouncement.expires_at.isnot(None),
            PlatformAnnouncement.expires_at <= now,
        )
        .all()
    )
    if not stale:
        return
    for a in stale:
        a.active = False
        a.deactivated_at = now
    db.commit()
    cache_delete(CACHE_KEY)


def list_active(db: Session) -> list[dict[str, Any]]:
    _expire_stale(db)
    cached = cache_get(CACHE_KEY)
    if isinstance(cached, list):
        return cached
    rows = (
        db.query(PlatformAnnouncement)
        .filter(PlatformAnnouncement.active.is_(True))
        .order_by(PlatformAnnouncement.created_at.desc())
        .limit(20)
        .all()
    )
    data = [_to_dict(r) for r in rows]
    cache_set(CACHE_KEY, data, ttl=15)
    return data


def list_all(db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    _expire_stale(db)
    rows = (
        db.query(PlatformAnnouncement)
        .order_by(PlatformAnnouncement.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_to_dict(r) for r in rows]


def create_announcement(
    db: Session,
    *,
    title: str,
    message: str,
    severity: str = "info",
    created_by_email: str = "",
    ttl_minutes: Optional[int] = None,
) -> dict[str, Any]:
    title = (title or "").strip() or "Aviso de plataforma"
    message = (message or "").strip()
    if not message:
        raise HTTPException(400, "El mensaje del aviso es obligatorio.")
    if len(message) > 2000:
        raise HTTPException(400, "Mensaje demasiado largo (máx. 2000 caracteres).")
    sev = (severity or "info").strip().lower()
    if sev not in {"info", "warn", "danger", "ok"}:
        sev = "info"
    expires = None
    if ttl_minutes and int(ttl_minutes) > 0:
        expires = datetime.utcnow() + timedelta(minutes=int(ttl_minutes))
    row = PlatformAnnouncement(
        title=title[:200],
        message=message,
        severity=sev,
        active=True,
        created_by_email=(created_by_email or "")[:255],
        expires_at=expires,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    cache_delete(CACHE_KEY)
    return _to_dict(row)


def deactivate(db: Session, announcement_id: UUID) -> dict[str, Any]:
    row = db.query(PlatformAnnouncement).filter(PlatformAnnouncement.id == announcement_id).first()
    if not row:
        raise HTTPException(404, "Aviso no encontrado.")
    row.active = False
    row.deactivated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    cache_delete(CACHE_KEY)
    return _to_dict(row)


def deactivate_all(db: Session) -> int:
    now = datetime.utcnow()
    rows = db.query(PlatformAnnouncement).filter(PlatformAnnouncement.active.is_(True)).all()
    for r in rows:
        r.active = False
        r.deactivated_at = now
    n = len(rows)
    if n:
        db.commit()
        cache_delete(CACHE_KEY)
    return n
