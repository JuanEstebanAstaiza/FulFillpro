from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from backend.app.database import get_db
from backend.app.dependencies import require_admin
from backend.app.models.audit import AccessLog, SecurityEvent
from backend.app.models.device import Device
from backend.app.models.license import License
from backend.app.models.order import Order
from backend.app.models.user import User
from backend.app.schemas.auth import UserOut
from backend.app.schemas.license import LicenseCreate, LicenseOut, LicenseUpdate
from backend.app.services import license_service
from backend.app.services.audit_service import log_access

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Licencias ──────────────────────────────────────────────


@router.get("/licenses")
def list_licenses(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    lics = (
        db.query(License)
        .options(joinedload(License.devices))
        .order_by(License.created_at.desc())
        .all()
    )
    return [license_service.license_to_dict(db, l) for l in lics]


@router.post("/licenses")
def create_license(
    body: LicenseCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    lic = license_service.create_license(db, body.model_dump(exclude_none=True))
    log_access(
        db,
        event_type="admin",
        detail=f"Licencia creada {lic.code}",
        user_id=admin.id,
        license_code=lic.code,
        label=lic.label,
    )
    return license_service.license_to_dict(db, lic)


@router.patch("/licenses/{license_id}")
def update_license(
    license_id: UUID,
    body: LicenseUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    lic = db.query(License).options(joinedload(License.devices)).filter(License.id == license_id).first()
    if not lic:
        raise HTTPException(404, "Licencia no encontrada.")
    lic = license_service.update_license(db, lic, body.model_dump(exclude_unset=True))
    log_access(
        db,
        event_type="admin",
        detail=f"Licencia actualizada {lic.code}",
        user_id=admin.id,
        license_code=lic.code,
    )
    return license_service.license_to_dict(db, lic)


@router.post("/licenses/{license_id}/assign/{user_id}")
def assign_license(
    license_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    lic = db.query(License).options(joinedload(License.devices)).filter(License.id == license_id).first()
    if not lic:
        raise HTTPException(404, "Licencia no encontrada.")
    lic = license_service.assign_license(db, lic, user_id)
    log_access(
        db,
        event_type="admin",
        detail=f"Licencia {lic.code} asignada a {user_id}",
        user_id=admin.id,
        license_code=lic.code,
    )
    return license_service.license_to_dict(db, lic)


@router.post("/licenses/{license_id}/toggle")
def toggle_license(
    license_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    lic = db.query(License).options(joinedload(License.devices)).filter(License.id == license_id).first()
    if not lic:
        raise HTTPException(404, "Licencia no encontrada.")
    lic.active = not lic.active
    db.commit()
    db.refresh(lic)
    return license_service.license_to_dict(db, lic)


@router.post("/licenses/{license_id}/reset-uses")
def reset_uses(
    license_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    lic = db.query(License).options(joinedload(License.devices)).filter(License.id == license_id).first()
    if not lic:
        raise HTTPException(404, "Licencia no encontrada.")
    lic.uses = 0
    db.commit()
    return license_service.license_to_dict(db, lic)


@router.post("/licenses/{license_id}/renew")
def renew(
    license_id: UUID,
    days: int = 30,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    from datetime import date

    lic = db.query(License).options(joinedload(License.devices)).filter(License.id == license_id).first()
    if not lic:
        raise HTTPException(404, "Licencia no encontrada.")
    base = lic.expiry if lic.expiry and lic.expiry > date.today() else date.today()
    lic.expiry = base + timedelta(days=days)
    db.commit()
    return license_service.license_to_dict(db, lic)


@router.post("/licenses/{license_id}/release-devices")
def release_devices(
    license_id: UUID,
    device_id: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    lic = db.query(License).options(joinedload(License.devices)).filter(License.id == license_id).first()
    if not lic:
        raise HTTPException(404, "Licencia no encontrada.")
    license_service.release_device(db, lic, device_id)
    db.refresh(lic)
    return license_service.license_to_dict(db, lic)


@router.delete("/licenses/{license_id}")
def delete_license(
    license_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    lic = db.query(License).filter(License.id == license_id).first()
    if not lic:
        raise HTTPException(404, "Licencia no encontrada.")
    code = lic.code
    db.delete(lic)
    db.commit()
    log_access(db, event_type="admin", detail=f"Licencia eliminada {code}", user_id=admin.id)
    return {"ok": True}


@router.get("/license-templates")
def templates(_: User = Depends(require_admin)):
    return license_service.TEMPLATES


# ── Usuarios ───────────────────────────────────────────────


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).order_by(User.created_at.desc()).all()


@router.post("/users/{user_id}/toggle")
def toggle_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Usuario no encontrado.")
    if user.id == admin.id:
        raise HTTPException(400, "No puedes desactivarte a ti mismo.")
    user.is_active = not user.is_active
    db.commit()
    return UserOut.model_validate(user)


# ── Monitoreo / incidentes ─────────────────────────────────


@router.get("/monitoring/overview")
def monitoring_overview(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    total_users = db.query(func.count(User.id)).scalar() or 0
    active_licenses = db.query(func.count(License.id)).filter(License.active.is_(True)).scalar() or 0
    orders_today = (
        db.query(func.count(Order.id)).filter(Order.created_at >= day_ago).scalar() or 0
    )
    orders_week = (
        db.query(func.count(Order.id)).filter(Order.created_at >= week_ago).scalar() or 0
    )
    failed_week = (
        db.query(func.count(Order.id))
        .filter(Order.status == "failed", Order.created_at >= week_ago)
        .scalar()
        or 0
    )
    open_incidents = (
        db.query(func.count(SecurityEvent.id)).filter(SecurityEvent.resolved == 0).scalar() or 0
    )
    critical_open = (
        db.query(func.count(SecurityEvent.id))
        .filter(SecurityEvent.resolved == 0, SecurityEvent.severity == "critical")
        .scalar()
        or 0
    )
    devices = db.query(func.count(Device.id)).filter(Device.is_active.is_(True)).scalar() or 0

    by_status = (
        db.query(Order.status, func.count(Order.id)).group_by(Order.status).all()
    )
    top_licenses = (
        db.query(License.code, License.label, License.uses, License.limit_uses)
        .order_by(License.uses.desc())
        .limit(10)
        .all()
    )

    return {
        "total_users": total_users,
        "active_licenses": active_licenses,
        "active_devices": devices,
        "orders_today": orders_today,
        "orders_week": orders_week,
        "failed_week": failed_week,
        "open_incidents": open_incidents,
        "critical_open": critical_open,
        "orders_by_status": {s: c for s, c in by_status},
        "top_licenses_by_use": [
            {"code": c, "label": l, "uses": u, "limit": lim}
            for c, l, u, lim in top_licenses
        ],
    }


@router.get("/monitoring/logs")
def access_logs(
    limit: int = Query(100, le=500),
    event_type: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = db.query(AccessLog)
    if event_type:
        q = q.filter(AccessLog.event_type == event_type)
    rows = q.order_by(AccessLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "user_id": str(r.user_id) if r.user_id else None,
            "license_code": r.license_code,
            "label": r.label,
            "event_type": r.event_type,
            "detail": r.detail,
            "device_id": r.device_id,
            "ip": r.ip,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/monitoring/incidents")
def incidents(
    resolved: Optional[int] = None,
    severity: Optional[str] = None,
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = db.query(SecurityEvent)
    if resolved is not None:
        q = q.filter(SecurityEvent.resolved == resolved)
    if severity:
        q = q.filter(SecurityEvent.severity == severity)
    rows = q.order_by(SecurityEvent.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "severity": r.severity,
            "category": r.category,
            "title": r.title,
            "detail": r.detail,
            "user_id": str(r.user_id) if r.user_id else None,
            "license_code": r.license_code,
            "ip": r.ip,
            "meta": r.meta or {},
            "resolved": r.resolved,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/monitoring/incidents/{event_id}/resolve")
def resolve_incident(
    event_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    ev = db.query(SecurityEvent).filter(SecurityEvent.id == event_id).first()
    if not ev:
        raise HTTPException(404, "Incidente no encontrado.")
    ev.resolved = 1
    db.commit()
    log_access(
        db,
        event_type="admin",
        detail=f"Incidente #{event_id} resuelto",
        user_id=admin.id,
    )
    return {"ok": True}
