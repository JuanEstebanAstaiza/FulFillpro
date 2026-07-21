from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from starlette.background import BackgroundTask

from backend.app.database import get_db
from backend.app.dependencies import require_admin
from backend.app.models.audit import AccessLog, SecurityEvent
from backend.app.models.device import Device
from backend.app.models.license import License
from backend.app.models.order import Order
from backend.app.models.user import User
from backend.app.schemas.auth import UserOut
from backend.app.schemas.license import LicenseCreate, LicenseOut, LicenseUpdate
from backend.app.services import backup_service, license_service
from backend.app.services.audit_service import log_access, log_security

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

    # Empresas con actividad reciente
    companies_with_orders_7d = (
        db.query(func.count(func.distinct(Order.client_code)))
        .filter(Order.created_at >= week_ago, Order.client_code != "")
        .scalar()
        or 0
    )
    companies_total = (
        db.query(func.count(func.distinct(User.client_code)))
        .filter(User.client_code != "", User.role != "admin")
        .scalar()
        or 0
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
        "companies_total": companies_total,
        "companies_active_7d": companies_with_orders_7d,
        "companies_inactive": max(companies_total - companies_with_orders_7d, 0),
        "orders_by_status": {s: c for s, c in by_status},
        "top_licenses_by_use": [
            {"code": c, "label": l, "uses": u, "limit": lim}
            for c, l, u, lim in top_licenses
        ],
    }


@router.get("/monitoring/companies")
def monitoring_companies(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """
    Métricas de uso por empresa: si están activas, dormidas o nunca han procesado.
    """
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # Usuarios no-plataforma agrupados por client_code
    users = (
        db.query(User)
        .filter(User.role != "admin", User.client_code != "")
        .all()
    )
    by_code: dict[str, dict] = {}
    for u in users:
        code = u.client_code
        if code not in by_code:
            by_code[code] = {
                "client_code": code,
                "company_name": u.company_name or code,
                "users": 0,
                "active_users": 0,
                "last_login": None,
                "user_ids": [],
            }
        row = by_code[code]
        row["users"] += 1
        row["user_ids"].append(u.id)
        if u.is_active:
            row["active_users"] += 1
        if u.company_name:
            row["company_name"] = u.company_name
        if u.last_login and (row["last_login"] is None or u.last_login > row["last_login"]):
            row["last_login"] = u.last_login

    # Licencias por owner → client_code
    licenses = db.query(License).all()
    owner_to_code = {u.id: u.client_code for u in users}
    for lic in licenses:
        code = owner_to_code.get(lic.owner_user_id) if lic.owner_user_id else None
        if not code and lic.company_name:
            # emparejar por company_name
            for c, data in by_code.items():
                if data["company_name"] == lic.company_name:
                    code = c
                    break
        if not code:
            # licencia sin empresa de usuarios aún
            key = f"LIC:{lic.code}"
            if key not in by_code:
                by_code[key] = {
                    "client_code": "",
                    "company_name": lic.company_name or lic.label or lic.code,
                    "users": 0,
                    "active_users": 0,
                    "last_login": None,
                    "user_ids": [],
                }
            code = key
        data = by_code.setdefault(
            code,
            {
                "client_code": code if not str(code).startswith("LIC:") else "",
                "company_name": lic.company_name or code,
                "users": 0,
                "active_users": 0,
                "last_login": None,
                "user_ids": [],
            },
        )
        data.setdefault("licenses", [])
        data["licenses"].append(
            {
                "code": lic.code,
                "label": lic.label,
                "uses": lic.uses or 0,
                "limit_uses": lic.limit_uses or 0,
                "active": lic.active,
                "last_access": lic.last_access.isoformat() if lic.last_access else None,
            }
        )

    # Agregar órdenes por client_code
    order_stats = (
        db.query(
            Order.client_code,
            func.count(Order.id),
            func.max(Order.created_at),
            func.sum(Order.priority_count),
        )
        .filter(Order.client_code != "")
        .group_by(Order.client_code)
        .all()
    )
    order_map = {c: {"total": n, "last": last, "priority_sum": int(p or 0)} for c, n, last, p in order_stats}

    orders_7d = (
        db.query(Order.client_code, func.count(Order.id))
        .filter(Order.created_at >= week_ago, Order.client_code != "")
        .group_by(Order.client_code)
        .all()
    )
    map_7d = {c: n for c, n in orders_7d}

    orders_30d = (
        db.query(Order.client_code, func.count(Order.id))
        .filter(Order.created_at >= month_ago, Order.client_code != "")
        .group_by(Order.client_code)
        .all()
    )
    map_30d = {c: n for c, n in orders_30d}

    orders_1d = (
        db.query(Order.client_code, func.count(Order.id))
        .filter(Order.created_at >= day_ago, Order.client_code != "")
        .group_by(Order.client_code)
        .all()
    )
    map_1d = {c: n for c, n in orders_1d}

    result = []
    for code, data in by_code.items():
        client = data["client_code"] or ""
        # Para claves LIC:xxx no hay client_code real
        o = order_map.get(client, {"total": 0, "last": None, "priority_sum": 0})
        last_order = o["last"]
        n7 = map_7d.get(client, 0)
        n30 = map_30d.get(client, 0)
        n1 = map_1d.get(client, 0)

        if n7 > 0:
            health = "active"
            health_label = "Activa (7d)"
        elif n30 > 0:
            health = "warm"
            health_label = "Uso reciente (30d)"
        elif o["total"] > 0:
            health = "dormant"
            health_label = "Inactiva (+30d)"
        else:
            health = "never"
            health_label = "Sin uso"

        lics = data.get("licenses") or []
        total_uses = sum(x.get("uses", 0) for x in lics)

        result.append(
            {
                "client_code": client,
                "company_name": data["company_name"],
                "users": data["users"],
                "active_users": data["active_users"],
                "last_login": data["last_login"].isoformat() if data["last_login"] else None,
                "orders_total": o["total"] or 0,
                "orders_today": n1,
                "orders_7d": n7,
                "orders_30d": n30,
                "priority_orders_sum": o["priority_sum"],
                "last_order_at": last_order.isoformat() if last_order else None,
                "license_uses": total_uses,
                "licenses": lics,
                "health": health,
                "health_label": health_label,
            }
        )

    # Orden: activas primero, luego por última orden
    order_health = {"active": 0, "warm": 1, "dormant": 2, "never": 3}
    result.sort(
        key=lambda r: (
            order_health.get(r["health"], 9),
            -(r["orders_7d"] or 0),
            r["company_name"] or "",
        )
    )
    return {
        "items": result,
        "summary": {
            "total": len(result),
            "active": sum(1 for r in result if r["health"] == "active"),
            "warm": sum(1 for r in result if r["health"] == "warm"),
            "dormant": sum(1 for r in result if r["health"] == "dormant"),
            "never": sum(1 for r in result if r["health"] == "never"),
        },
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


# ── Backup / restore (owners plataforma) ───────────────────


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@router.get("/backup/info")
def backup_info(
    include_storage: bool = Query(True),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Estimación de tamaño y filas antes de generar el backup."""
    return backup_service.estimate_backup(db, include_storage=include_storage)


@router.post("/backup/download")
def backup_download(
    request: Request,
    include_storage: bool = Query(True, description="Incluir archivos Excel del storage"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Genera y descarga un ZIP de emergencia (BD + storage opcional).
    Solo platform admin.
    """
    try:
        path, meta = backup_service.create_backup_zip(
            db,
            include_storage=include_storage,
            created_by=admin.email,
        )
    except Exception as e:
        raise HTTPException(500, f"No se pudo generar el backup: {e}") from e

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"fulfillpro-backup-{stamp}.zip"
    ip = request.client.host if request.client else ""
    log_access(
        db,
        event_type="admin_backup",
        detail=(
            f"Backup descargado · rows={meta.get('total_rows')} · "
            f"storage={'sí' if include_storage else 'no'} · "
            f"{meta.get('archive_mb', '?')} MB"
        ),
        user_id=admin.id,
        ip=ip,
    )
    log_security(
        db,
        title="Backup de plataforma descargado",
        detail=f"Por {admin.email} · {filename} · storage={include_storage}",
        severity="info",
        category="operational",
        user_id=admin.id,
        ip=ip,
        meta={"include_storage": include_storage, "archive_mb": meta.get("archive_mb")},
    )

    return FileResponse(
        path,
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(_unlink_quiet, str(path)),
        headers={
            "X-Backup-Rows": str(meta.get("total_rows") or 0),
            "Access-Control-Expose-Headers": "Content-Disposition, X-Backup-Rows",
        },
    )


@router.post("/backup/restore")
async def backup_restore(
    request: Request,
    file: UploadFile = File(...),
    confirm_phrase: str = Form(...),
    include_storage: str = Form("true"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Restaura un backup ZIP de emergencia.
    Requiere confirm_phrase = RESTAURAR (mayúsculas).
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Debes subir un archivo .zip de backup FulfillPro.")

    do_storage = str(include_storage).lower() in {"1", "true", "yes", "on"}
    ip = request.client.host if request.client else ""
    tmp_path: Optional[Path] = None
    try:
        tmp_path = await backup_service.save_upload_to_temp(file)
        peek = backup_service.peek_backup_zip(tmp_path)
        result = backup_service.restore_backup_zip(
            db,
            tmp_path,
            confirm_phrase=confirm_phrase,
            include_storage=do_storage,
            created_by=admin.email,
        )
        # Tras restore, el admin puede haber cambiado; loguear en nueva sesión DB
        try:
            log_security(
                db,
                title="Restauración de backup ejecutada",
                detail=(
                    f"Por {admin.email} · source={peek.get('created_at')} · "
                    f"rows={result.get('total_rows')} · storage_files={result.get('storage_files_restored')}"
                ),
                severity="critical",
                category="operational",
                user_id=admin.id,
                ip=ip,
                meta=result,
            )
        except Exception:
            pass
        return result
    finally:
        if tmp_path is not None:
            _unlink_quiet(str(tmp_path))


@router.post("/backup/inspect")
async def backup_inspect(
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
):
    """Inspecciona un ZIP de backup sin restaurarlo."""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Debes subir un archivo .zip.")
    tmp_path = await backup_service.save_upload_to_temp(file)
    try:
        return backup_service.peek_backup_zip(tmp_path)
    finally:
        _unlink_quiet(str(tmp_path))
