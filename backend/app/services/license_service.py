from __future__ import annotations

import secrets
import string
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from backend.app.config import get_settings
from backend.app.core.security import normalize_device_id
from backend.app.models.device import Device
from backend.app.models.license import License
from backend.app.models.order import Order
from backend.app.models.user import User
from backend.app.redis_client import cache_delete, cache_get, cache_set
from backend.app.services.audit_service import log_access, log_security

# Plantillas de licencias predefinidas
TEMPLATES: dict[str, dict[str, Any]] = {
    "trial": {
        "type": "trial",
        "label": "Prueba gratuita",
        "max_devices": 3,
        "limit_uses": 50,
        "daily_limit": 3,
        "duration_days": 7,
        "count_toward_global": True,
        "enforce_daily_limit": True,
    },
    "standard": {
        "type": "standard",
        "label": "Plan Standard",
        "max_devices": 5,
        "limit_uses": 500,
        "daily_limit": 50,
        "duration_days": 30,
        "count_toward_global": True,
        "enforce_daily_limit": True,
    },
    "pro": {
        "type": "pro",
        "label": "Plan Pro",
        "max_devices": 15,
        "limit_uses": 0,  # ilimitado
        "daily_limit": 200,
        "duration_days": 365,
        "count_toward_global": True,
        "enforce_daily_limit": True,
    },
    "enterprise": {
        "type": "enterprise",
        "label": "Plan Enterprise",
        "max_devices": 999,
        "limit_uses": 0,
        "daily_limit": 0,
        "duration_days": 365,
        "count_toward_global": True,
        "enforce_daily_limit": False,
    },
}


def generate_license_code(prefix: str = "FP") -> str:
    alphabet = string.ascii_uppercase + string.digits
    part = lambda n: "".join(secrets.choice(alphabet) for _ in range(n))
    return f"{prefix}-{part(4)}-{part(4)}"


def days_left(expiry: Optional[date]) -> Optional[int]:
    if not expiry:
        return None
    return (expiry - date.today()).days


def uses_today(db: Session, license_id: UUID) -> int:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(func.count(Order.id))
        .filter(
            Order.license_id == license_id,
            Order.status == "completed",
            Order.counted_toward_quota.is_(True),
            Order.processed_at >= today_start,
        )
        .scalar()
        or 0
    )


def active_devices_count(db: Session, license_id: UUID) -> int:
    return db.query(Device).filter(Device.license_id == license_id, Device.is_active.is_(True)).count()


def license_to_dict(db: Session, lic: License, include_devices: bool = True) -> dict:
    devices = []
    if include_devices:
        for d in lic.devices:
            devices.append(
                {
                    "id": d.id,
                    "device_id": d.device_id,
                    "device_name": d.device_name,
                    "device_fingerprint": d.device_fingerprint or "",
                    "first_seen": d.first_seen,
                    "last_seen": d.last_seen,
                    "is_active": d.is_active,
                }
            )
    return {
        "id": lic.id,
        "code": lic.code,
        "label": lic.label,
        "type": lic.type,
        "company_name": lic.company_name or "",
        "owner_user_id": lic.owner_user_id,
        "max_devices": lic.max_devices,
        "limit_uses": lic.limit_uses,
        "uses": lic.uses or 0,
        "daily_limit": lic.daily_limit or 0,
        "uses_today": uses_today(db, lic.id),
        "expiry": lic.expiry,
        "days_left": days_left(lic.expiry),
        "active": lic.active,
        "count_toward_global": lic.count_toward_global,
        "enforce_daily_limit": lic.enforce_daily_limit,
        "features": lic.features or {},
        "notes": lic.notes or "",
        "devices_count": sum(1 for d in lic.devices if d.is_active),
        "devices": devices,
        "created_at": lic.created_at,
        "last_access": lic.last_access,
        "assigned_at": lic.assigned_at,
    }


def create_license(db: Session, data: dict) -> License:
    payload = dict(data)
    template_name = payload.pop("template", None)
    duration_days = payload.pop("duration_days", None)

    if template_name and template_name in TEMPLATES:
        base = TEMPLATES[template_name].copy()
        tpl_duration = base.pop("duration_days", None)
        for k, v in base.items():
            payload.setdefault(k, v)
        if duration_days is None:
            duration_days = tpl_duration

    code = (payload.get("code") or "").upper().strip() or generate_license_code()
    if db.query(License).filter(License.code == code).first():
        raise HTTPException(400, "Ese código de licencia ya existe.")

    expiry = payload.get("expiry")
    if not expiry and duration_days:
        expiry = date.today() + timedelta(days=int(duration_days))

    owner_id = payload.get("owner_user_id")
    lic = License(
        code=code,
        label=payload.get("label", ""),
        type=payload.get("type", "standard"),
        company_name=payload.get("company_name", ""),
        owner_user_id=owner_id,
        max_devices=int(payload.get("max_devices") or 3),
        limit_uses=int(payload.get("limit_uses") or 0),
        daily_limit=int(payload.get("daily_limit") or 0),
        uses=0,
        expiry=expiry,
        active=True,
        count_toward_global=payload.get("count_toward_global", True),
        enforce_daily_limit=payload.get("enforce_daily_limit", True),
        features=payload.get("features") or {},
        notes=payload.get("notes", ""),
        assigned_at=datetime.utcnow() if owner_id else None,
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)
    return lic


def update_license(db: Session, lic: License, data: dict) -> License:
    for field in (
        "label",
        "type",
        "company_name",
        "max_devices",
        "limit_uses",
        "daily_limit",
        "expiry",
        "active",
        "count_toward_global",
        "enforce_daily_limit",
        "features",
        "notes",
    ):
        if field in data and data[field] is not None:
            setattr(lic, field, data[field])

    if "owner_user_id" in data:
        lic.owner_user_id = data["owner_user_id"]
        if data["owner_user_id"]:
            lic.assigned_at = datetime.utcnow()

    db.commit()
    db.refresh(lic)
    cache_delete(f"license:{lic.code}")
    return lic


def assign_license(db: Session, lic: License, user_id: UUID) -> License:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Usuario no encontrado.")
    lic.owner_user_id = user_id
    if not lic.company_name and user.company_name:
        lic.company_name = user.company_name
    lic.assigned_at = datetime.utcnow()
    db.commit()
    db.refresh(lic)
    cache_delete(f"license:{lic.code}")
    return lic


def check_license_quotas(db: Session, lic: License) -> None:
    """Valida estado, caducidad, cupo global y cupo diario. Lanza HTTPException si no aplica."""
    features = lic.features or {}

    if not lic.active:
        raise HTTPException(403, "Licencia desactivada. Contacta al administrador.")

    dl = days_left(lic.expiry)
    if dl is not None and dl < 0:
        raise HTTPException(403, f"Licencia expirada el {lic.expiry}.")

    unlimited = bool(features.get("unlimited_orders")) or not lic.count_toward_global
    if not unlimited and lic.limit_uses > 0 and (lic.uses or 0) >= lic.limit_uses:
        raise HTTPException(403, "Límite global de órdenes de la licencia alcanzado.")

    skip_daily = bool(features.get("skip_daily_limit")) or not lic.enforce_daily_limit
    if not skip_daily and lic.daily_limit > 0:
        today = uses_today(db, lic.id)
        if today >= lic.daily_limit:
            raise HTTPException(
                403,
                f"Límite diario alcanzado ({lic.daily_limit} órdenes/día). Intenta mañana.",
            )


def activate_device(
    db: Session,
    *,
    user: User,
    code: str,
    device_id: str,
    device_name: str = "",
    device_fingerprint: str = "",
    device_soft: str = "",
    ip: str = "",
) -> dict:
    code = code.upper().strip()
    device_id = normalize_device_id(device_id)
    if not device_id:
        raise HTTPException(400, "Debes indicar el identificador del equipo.")

    lic = (
        db.query(License)
        .options(joinedload(License.devices))
        .filter(License.code == code)
        .first()
    )
    if not lic:
        log_security(
            db,
            title="Activación con código inválido",
            detail=f"Código {code}",
            severity="warning",
            category="license",
            user_id=user.id,
            ip=ip,
        )
        raise HTTPException(400, "Código de licencia no reconocido.")

    # Asignar al usuario si aún no tiene dueño, o verificar propiedad
    if lic.owner_user_id and lic.owner_user_id != user.id and user.role != "admin":
        # Permitir si es el mismo cliente sin dueño estricto: solo admin reasigna
        if user.role != "admin":
            raise HTTPException(403, "Esta licencia está asignada a otra cuenta.")

    if not lic.owner_user_id:
        lic.owner_user_id = user.id
        lic.assigned_at = datetime.utcnow()
        if not lic.company_name and user.company_name:
            lic.company_name = user.company_name

    check_license_quotas(db, lic)

    settings = get_settings()
    now = datetime.utcnow()
    active_devs = [d for d in lic.devices if d.is_active]

    # 1) Mismo device_id ya registrado
    for d in active_devs:
        if d.device_id == device_id:
            d.last_seen = now
            d.device_name = device_name or d.device_name
            if device_fingerprint:
                d.device_fingerprint = device_fingerprint
            if device_soft:
                d.device_soft = device_soft
            lic.last_access = now
            db.commit()
            log_access(
                db,
                event_type="activate",
                detail=f"Equipo conocido {device_name or device_id}",
                user_id=user.id,
                license_code=lic.code,
                label=lic.label,
                device_id=device_id,
                ip=ip,
            )
            cache_delete(f"license:{lic.code}")
            return {"ok": True, "device_status": "known", "license": license_to_dict(db, lic)}

    # 2) Re-vincular por fingerprint soft (borraron cache local)
    if device_soft:
        for d in active_devs:
            if d.device_soft and d.device_soft == device_soft:
                d.device_id = device_id
                d.last_seen = now
                d.device_name = device_name or d.device_name
                if device_fingerprint:
                    d.device_fingerprint = device_fingerprint
                lic.last_access = now
                db.commit()
                log_access(
                    db,
                    event_type="activate",
                    detail=f"Re-vinculado {device_name or device_id}",
                    user_id=user.id,
                    license_code=lic.code,
                    label=lic.label,
                    device_id=device_id,
                    ip=ip,
                )
                cache_delete(f"license:{lic.code}")
                return {"ok": True, "device_status": "rebound", "license": license_to_dict(db, lic)}

    # 3) Cupo de dispositivos
    if len(active_devs) >= (lic.max_devices or 1):
        # Reciclar inactivos por antigüedad
        stale = [
            d
            for d in active_devs
            if d.last_seen and (now - d.last_seen).days >= settings.device_stale_days
        ]
        if stale:
            oldest = sorted(stale, key=lambda x: x.last_seen or datetime.min)[0]
            oldest.is_active = False
        else:
            log_security(
                db,
                title="Cupo de dispositivos lleno",
                detail=f"Licencia {lic.code} max={lic.max_devices} intento {device_id}",
                severity="warning",
                category="license",
                user_id=user.id,
                license_code=lic.code,
                ip=ip,
            )
            raise HTTPException(
                403,
                f"Esta licencia ya tiene {lic.max_devices} equipo(s) registrados. "
                "Pide al administrador liberar un dispositivo o ampliar el cupo.",
            )

    new_dev = Device(
        license_id=lic.id,
        device_id=device_id,
        device_name=device_name,
        device_fingerprint=device_fingerprint,
        device_soft=device_soft,
        first_seen=now,
        last_seen=now,
        is_active=True,
    )
    db.add(new_dev)
    lic.last_access = now
    db.commit()
    db.refresh(lic)
    log_access(
        db,
        event_type="activate",
        detail=f"Nuevo equipo {device_name or device_id}",
        user_id=user.id,
        license_code=lic.code,
        label=lic.label,
        device_id=device_id,
        ip=ip,
    )
    cache_delete(f"license:{lic.code}")
    return {"ok": True, "device_status": "new", "license": license_to_dict(db, lic)}


def assert_device_authorized(
    db: Session,
    *,
    user: User,
    license_code: str,
    device_id: str,
    device_soft: str = "",
) -> License:
    code = license_code.upper().strip()
    device_id = normalize_device_id(device_id)

    cache_key = f"license:{code}:dev:{device_id}"
    cached = cache_get(cache_key)
    if cached and cached.get("ok"):
        lic = db.query(License).filter(License.code == code).first()
        if lic:
            check_license_quotas(db, lic)
            return lic

    lic = (
        db.query(License)
        .options(joinedload(License.devices))
        .filter(License.code == code)
        .first()
    )
    if not lic:
        raise HTTPException(403, "Licencia no válida.")

    if lic.owner_user_id and lic.owner_user_id != user.id and user.role != "admin":
        raise HTTPException(403, "Licencia no asignada a este usuario.")

    check_license_quotas(db, lic)

    authorized = False
    for d in lic.devices:
        if not d.is_active:
            continue
        if d.device_id == device_id:
            authorized = True
            d.last_seen = datetime.utcnow()
            break
        if device_soft and d.device_soft == device_soft:
            d.device_id = device_id
            d.last_seen = datetime.utcnow()
            authorized = True
            break

    if not authorized:
        raise HTTPException(
            403,
            "Equipo no autorizado. Activa la licencia con el identificador de este equipo.",
        )

    lic.last_access = datetime.utcnow()
    db.commit()
    cache_set(cache_key, {"ok": True}, ttl=60)
    return lic


def consume_quota(db: Session, lic: License, *, count: bool = True) -> bool:
    """Incrementa el contador global si la licencia descuenta cupo. Retorna si contó."""
    features = lic.features or {}
    if not count:
        return False
    if not lic.count_toward_global:
        return False
    if features.get("independent_upload") or features.get("unlimited_orders"):
        return False
    lic.uses = (lic.uses or 0) + 1
    db.commit()
    cache_delete(f"license:{lic.code}")
    return True


def release_device(db: Session, lic: License, device_id: Optional[str] = None) -> None:
    if device_id:
        did = normalize_device_id(device_id)
        for d in lic.devices:
            if d.device_id == did:
                d.is_active = False
    else:
        for d in lic.devices:
            d.is_active = False
    db.commit()
    cache_delete(f"license:{lic.code}")


def get_user_license(db: Session, user: User) -> Optional[License]:
    """
    Licencia efectiva del usuario por empresa:
    1) Licencia de la que es dueño
    2) Licencia activa de otro usuario con el mismo client_code (misma empresa)
    """
    owned = (
        db.query(License)
        .options(joinedload(License.devices))
        .filter(License.owner_user_id == user.id, License.active.is_(True))
        .order_by(License.created_at.desc())
        .first()
    )
    if owned:
        return owned

    if user.client_code:
        peer_ids = [
            r[0]
            for r in db.query(User.id)
            .filter(User.client_code == user.client_code, User.is_active.is_(True))
            .all()
        ]
        if peer_ids:
            shared = (
                db.query(License)
                .options(joinedload(License.devices))
                .filter(
                    License.owner_user_id.in_(peer_ids),
                    License.active.is_(True),
                )
                .order_by(License.created_at.desc())
                .first()
            )
            if shared:
                return shared
    return None


def assert_user_license(db: Session, user: User, license_code: Optional[str] = None) -> License:
    """Autoriza por cuenta de empresa (sin registro de equipos)."""
    lic: Optional[License] = None
    if license_code:
        code = license_code.upper().strip()
        lic = (
            db.query(License)
            .options(joinedload(License.devices))
            .filter(License.code == code)
            .first()
        )
        if not lic:
            raise HTTPException(403, "Licencia no válida.")
        # Solo la empresa dueña o admin
        if user.role != "admin":
            if lic.owner_user_id == user.id:
                pass
            elif user.client_code and lic.owner_user_id:
                owner = db.query(User).filter(User.id == lic.owner_user_id).first()
                if not owner or owner.client_code != user.client_code:
                    raise HTTPException(
                        403,
                        "Esta licencia pertenece a otra empresa. "
                        "No puedes usarla con tu cuenta.",
                    )
            elif lic.owner_user_id and lic.owner_user_id != user.id:
                raise HTTPException(403, "Licencia no asignada a tu empresa.")
    else:
        lic = get_user_license(db, user)
        if not lic:
            raise HTTPException(
                403,
                "Tu cuenta no tiene licencia activa. Contacta al administrador de tu empresa.",
            )

    check_license_quotas(db, lic)
    lic.last_access = datetime.utcnow()
    db.commit()
    return lic


def company_brand(lic: License, user: User) -> dict[str, str]:
    """Datos de distintivo corporativo para Excel y UI."""
    company = (lic.company_name or user.company_name or user.client_code or "Cliente").strip()
    code = (user.client_code or lic.code or "").strip()
    return {
        "company_name": company,
        "company_code": code,
        "license_code": lic.code,
        "brand_line": f"Documento exclusivo · {company} · Lic. {lic.code}",
        "footer_line": (
            f"Generado con FulfillPro para uso exclusivo de {company} "
            f"({code}) · Licencia {lic.code} · Prohibida su reventa o uso por terceros"
        ),
    }


def usage_summary(db: Session, lic: License) -> dict:
    """Resumen para dashboard: % uso, alertas, días restantes."""
    data = license_to_dict(db, lic, include_devices=False)
    limit = lic.limit_uses or 0
    uses = lic.uses or 0
    daily = lic.daily_limit or 0
    today = data["uses_today"]
    pct_global = round((uses / limit) * 100, 1) if limit > 0 else 0.0
    pct_daily = round((today / daily) * 100, 1) if daily > 0 else 0.0
    remaining = max(limit - uses, 0) if limit > 0 else None
    days = data["days_left"]

    warnings: list[str] = []
    if limit > 0 and pct_global >= 90:
        warnings.append(f"Quedan pocos usos del plan ({remaining} de {limit}).")
    elif limit > 0 and pct_global >= 75:
        warnings.append(f"Has usado el {pct_global}% del cupo global.")
    if daily > 0 and pct_daily >= 90:
        warnings.append(f"Casi alcanzas el límite diario ({today}/{daily}).")
    if days is not None and days <= 3:
        warnings.append(f"La licencia vence en {days} día(s).")
    if days is not None and days < 0:
        warnings.append("La licencia está vencida.")

    return {
        **data,
        "usage_percent": pct_global,
        "daily_percent": pct_daily,
        "remaining_uses": remaining,
        "warnings": warnings,
        "near_limit": bool(warnings),
    }
