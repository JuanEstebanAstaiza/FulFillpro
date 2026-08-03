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
from backend.app.models.license import License, LicenseTemplate
from backend.app.models.order import Order
from backend.app.models.user import User
from backend.app.redis_client import cache_delete, cache_get, cache_set
from backend.app.services.audit_service import log_access, log_security

# Semilla inicial si la tabla de plantillas está vacía (luego se editan en Ops)
DEFAULT_TEMPLATE_SEED: list[dict[str, Any]] = [
    {
        "slug": "trial",
        "name": "Prueba gratuita",
        "description": "7 días · 50 órdenes · 3/día",
        "license_type": "trial",
        "label_default": "Prueba gratuita",
        "max_devices": 3,
        "max_users": 3,
        "limit_uses": 50,
        "daily_limit": 3,
        "duration_days": 7,
        "count_toward_global": True,
        "enforce_daily_limit": True,
        "analytics_enabled": True,
        "analytics_weeks_retention": 4,
        "analytics_max_events_per_week": 5000,
        "analytics_storage_mb": 50,
        "sort_order": 10,
        "is_system": True,
    },
    {
        "slug": "standard",
        "name": "Standard (mensual)",
        "description": "30 días · 500 órdenes · 50/día",
        "license_type": "standard",
        "label_default": "Plan Standard",
        "max_devices": 5,
        "max_users": 10,
        "limit_uses": 500,
        "daily_limit": 50,
        "duration_days": 30,
        "count_toward_global": True,
        "enforce_daily_limit": True,
        "analytics_enabled": True,
        "analytics_weeks_retention": 12,
        "analytics_max_events_per_week": 30000,
        "analytics_storage_mb": 200,
        "sort_order": 20,
        "is_system": True,
    },
    {
        "slug": "pro",
        "name": "Pro (anual)",
        "description": "365 días · ilimitado global · 200/día",
        "license_type": "pro",
        "label_default": "Plan Pro",
        "max_devices": 15,
        "max_users": 25,
        "limit_uses": 0,
        "daily_limit": 200,
        "duration_days": 365,
        "count_toward_global": True,
        "enforce_daily_limit": True,
        "analytics_enabled": True,
        "analytics_weeks_retention": 26,
        "analytics_max_events_per_week": 100000,
        "analytics_storage_mb": 500,
        "sort_order": 30,
        "is_system": True,
    },
    {
        "slug": "enterprise",
        "name": "Enterprise",
        "description": "365 días · ilimitado · sin tope diario",
        "license_type": "enterprise",
        "label_default": "Plan Enterprise",
        "max_devices": 999,
        "max_users": 0,
        "limit_uses": 0,
        "daily_limit": 0,
        "duration_days": 365,
        "count_toward_global": True,
        "enforce_daily_limit": False,
        "analytics_enabled": True,
        "analytics_weeks_retention": 52,
        "analytics_max_events_per_week": 500000,
        "analytics_storage_mb": 2000,
        "sort_order": 40,
        "is_system": True,
    },
]

# Compat: algunos callers antiguos importan TEMPLATES (mapa estático de fallback)
TEMPLATES: dict[str, dict[str, Any]] = {
    row["slug"]: {
        "type": row["license_type"],
        "label": row["label_default"],
        "max_devices": row["max_devices"],
        "max_users": row.get("max_users", 0),
        "limit_uses": row["limit_uses"],
        "daily_limit": row["daily_limit"],
        "duration_days": row["duration_days"],
        "count_toward_global": row["count_toward_global"],
        "enforce_daily_limit": row["enforce_daily_limit"],
        "analytics_enabled": row["analytics_enabled"],
        "analytics_weeks_retention": row["analytics_weeks_retention"],
        "analytics_max_events_per_week": row["analytics_max_events_per_week"],
        "analytics_storage_mb": row["analytics_storage_mb"],
    }
    for row in DEFAULT_TEMPLATE_SEED
}


def _slugify(value: str) -> str:
    raw = (value or "").strip().lower()
    out = []
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_", "."}:
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:64] or secrets.token_hex(4)


def template_to_apply_dict(tpl: LicenseTemplate) -> dict[str, Any]:
    """Formato usado al crear/cambiar licencia desde plantilla."""
    return {
        "type": tpl.license_type or tpl.slug,
        "label": tpl.label_default or tpl.name,
        "max_devices": int(tpl.max_devices or 3),
        "max_users": int(getattr(tpl, "max_users", 0) or 0),
        "limit_uses": int(tpl.limit_uses or 0),
        "daily_limit": int(tpl.daily_limit or 0),
        "duration_days": int(tpl.duration_days or 30),
        "count_toward_global": bool(tpl.count_toward_global),
        "enforce_daily_limit": bool(tpl.enforce_daily_limit),
        "analytics_enabled": bool(tpl.analytics_enabled),
        "analytics_weeks_retention": int(tpl.analytics_weeks_retention or 12),
        "analytics_max_events_per_week": int(tpl.analytics_max_events_per_week or 50000),
        "analytics_storage_mb": int(tpl.analytics_storage_mb or 200),
        "features": dict(tpl.features or {}),
    }


def template_to_dict(tpl: LicenseTemplate) -> dict[str, Any]:
    return {
        "id": str(tpl.id),
        "slug": tpl.slug,
        "name": tpl.name,
        "description": tpl.description or "",
        "license_type": tpl.license_type,
        "label_default": tpl.label_default or "",
        "max_devices": tpl.max_devices,
        "max_users": int(getattr(tpl, "max_users", 0) or 0),
        "limit_uses": tpl.limit_uses,
        "daily_limit": tpl.daily_limit,
        "duration_days": tpl.duration_days,
        "count_toward_global": bool(tpl.count_toward_global),
        "enforce_daily_limit": bool(tpl.enforce_daily_limit),
        "analytics_enabled": bool(tpl.analytics_enabled),
        "analytics_weeks_retention": int(tpl.analytics_weeks_retention or 12),
        "analytics_max_events_per_week": int(tpl.analytics_max_events_per_week or 50000),
        "analytics_storage_mb": int(tpl.analytics_storage_mb or 200),
        "features": tpl.features or {},
        "is_active": bool(tpl.is_active),
        "sort_order": int(tpl.sort_order or 0),
        "is_system": bool(tpl.is_system),
        "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
        "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
        # alias cómodo para UI de creación
        "hint": (
            f"{tpl.duration_days or 0} días · "
            f"{'∞' if not tpl.limit_uses else tpl.limit_uses} órdenes · "
            f"{'∞' if not tpl.daily_limit else tpl.daily_limit}/día"
        ),
    }


def seed_license_templates(db: Session) -> int:
    """Crea plantillas por defecto si no existen (por slug). No pisa ediciones del admin."""
    created = 0
    for row in DEFAULT_TEMPLATE_SEED:
        exists = db.query(LicenseTemplate).filter(LicenseTemplate.slug == row["slug"]).first()
        if exists:
            continue
        db.add(
            LicenseTemplate(
                slug=row["slug"],
                name=row["name"],
                description=row.get("description") or "",
                license_type=row["license_type"],
                label_default=row.get("label_default") or row["name"],
                max_devices=row["max_devices"],
                max_users=int(row.get("max_users") or 0),
                limit_uses=row["limit_uses"],
                daily_limit=row["daily_limit"],
                duration_days=row["duration_days"],
                count_toward_global=row["count_toward_global"],
                enforce_daily_limit=row["enforce_daily_limit"],
                analytics_enabled=row.get("analytics_enabled", True),
                analytics_weeks_retention=row.get("analytics_weeks_retention", 12),
                analytics_max_events_per_week=row.get("analytics_max_events_per_week", 50000),
                analytics_storage_mb=row.get("analytics_storage_mb", 200),
                features={},
                is_active=True,
                sort_order=row.get("sort_order", 100),
                is_system=bool(row.get("is_system", True)),
            )
        )
        created += 1
    if created:
        db.commit()
    return created


def list_templates(db: Session, *, active_only: bool = False) -> list[LicenseTemplate]:
    q = db.query(LicenseTemplate)
    if active_only:
        q = q.filter(LicenseTemplate.is_active.is_(True))
    return q.order_by(LicenseTemplate.sort_order.asc(), LicenseTemplate.name.asc()).all()


def get_template_by_slug(db: Session, slug: str) -> Optional[LicenseTemplate]:
    if not slug:
        return None
    return (
        db.query(LicenseTemplate)
        .filter(LicenseTemplate.slug == slug.strip().lower())
        .first()
    )


def resolve_template_payload(db: Session, slug: str) -> Optional[dict[str, Any]]:
    """Resuelve plantilla desde BD; fallback a DEFAULT_TEMPLATE_SEED."""
    tpl = get_template_by_slug(db, slug)
    if tpl:
        if not tpl.is_active:
            raise HTTPException(400, f"La plantilla '{slug}' está desactivada.")
        return template_to_apply_dict(tpl)
    # fallback estático
    if slug in TEMPLATES:
        return TEMPLATES[slug].copy()
    return None


def create_template(db: Session, data: dict) -> LicenseTemplate:
    slug = _slugify(data.get("slug") or data.get("name") or "")
    if not slug:
        raise HTTPException(400, "Slug o nombre de plantilla requerido.")
    if db.query(LicenseTemplate).filter(LicenseTemplate.slug == slug).first():
        raise HTTPException(400, f"Ya existe una plantilla con slug '{slug}'.")

    tpl = LicenseTemplate(
        slug=slug,
        name=(data.get("name") or slug).strip(),
        description=(data.get("description") or "").strip(),
        license_type=(data.get("license_type") or data.get("type") or slug)[:32],
        label_default=(data.get("label_default") or data.get("label") or data.get("name") or slug).strip(),
        max_devices=int(data.get("max_devices") if data.get("max_devices") is not None else 5),
        max_users=int(data.get("max_users") if data.get("max_users") is not None else 0),
        limit_uses=int(data.get("limit_uses") if data.get("limit_uses") is not None else 0),
        daily_limit=int(data.get("daily_limit") if data.get("daily_limit") is not None else 0),
        duration_days=int(data.get("duration_days") if data.get("duration_days") is not None else 30),
        count_toward_global=bool(data.get("count_toward_global", True)),
        enforce_daily_limit=bool(data.get("enforce_daily_limit", True)),
        analytics_enabled=bool(data.get("analytics_enabled", True)),
        analytics_weeks_retention=int(data.get("analytics_weeks_retention") or 12),
        analytics_max_events_per_week=int(data.get("analytics_max_events_per_week") or 50000),
        analytics_storage_mb=int(data.get("analytics_storage_mb") or 200),
        features=data.get("features") or {},
        is_active=bool(data.get("is_active", True)),
        sort_order=int(data.get("sort_order") if data.get("sort_order") is not None else 100),
        is_system=False,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def update_template(db: Session, tpl: LicenseTemplate, data: dict) -> LicenseTemplate:
    # slug: solo si no es system o se permite renombrar custom
    if "slug" in data and data["slug"] is not None:
        new_slug = _slugify(str(data["slug"]))
        if new_slug != tpl.slug:
            if tpl.is_system:
                raise HTTPException(400, "No se puede renombrar el slug de una plantilla de sistema.")
            if db.query(LicenseTemplate).filter(LicenseTemplate.slug == new_slug).first():
                raise HTTPException(400, f"Ya existe la plantilla '{new_slug}'.")
            tpl.slug = new_slug

    field_map = {
        "name": "name",
        "description": "description",
        "license_type": "license_type",
        "type": "license_type",
        "label_default": "label_default",
        "label": "label_default",
        "max_devices": "max_devices",
        "max_users": "max_users",
        "limit_uses": "limit_uses",
        "daily_limit": "daily_limit",
        "duration_days": "duration_days",
        "count_toward_global": "count_toward_global",
        "enforce_daily_limit": "enforce_daily_limit",
        "analytics_enabled": "analytics_enabled",
        "analytics_weeks_retention": "analytics_weeks_retention",
        "analytics_max_events_per_week": "analytics_max_events_per_week",
        "analytics_storage_mb": "analytics_storage_mb",
        "features": "features",
        "is_active": "is_active",
        "sort_order": "sort_order",
    }
    for src, dest in field_map.items():
        if src in data and data[src] is not None:
            val = data[src]
            if dest in {
                "max_devices",
                "max_users",
                "limit_uses",
                "daily_limit",
                "duration_days",
                "analytics_weeks_retention",
                "analytics_max_events_per_week",
                "analytics_storage_mb",
                "sort_order",
            }:
                val = int(val)
            elif dest in {
                "count_toward_global",
                "enforce_daily_limit",
                "analytics_enabled",
                "is_active",
            }:
                val = bool(val)
            elif dest in {"name", "description", "license_type", "label_default"}:
                val = str(val).strip()
            setattr(tpl, dest, val)

    tpl.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(tpl)
    return tpl


def delete_template(db: Session, tpl: LicenseTemplate) -> None:
    if tpl.is_system:
        # Las de sistema se desactivan en lugar de borrar (evita romper referencias históricas)
        tpl.is_active = False
        tpl.updated_at = datetime.utcnow()
        db.commit()
        return
    db.delete(tpl)
    db.commit()


def generate_license_code(prefix: str = "FP") -> str:
    alphabet = string.ascii_uppercase + string.digits
    part = lambda n: "".join(secrets.choice(alphabet) for _ in range(n))
    return f"{prefix}-{part(4)}-{part(4)}"


def days_left(expiry: Optional[date]) -> Optional[int]:
    if not expiry:
        return None
    return (expiry - date.today()).days


def uses_today(db: Session, license_id: UUID) -> int:
    """
    Cupo diario: cuenta órdenes del día que ocupan cupo (completadas + en vuelo).
    Incluye queued/processing para que 100 encolados concurrentes no sobrepasen el plan.
    """
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(func.count(Order.id))
        .filter(
            Order.license_id == license_id,
            Order.created_at >= today_start,
            Order.status.in_(("completed", "processing", "queued", "uploaded")),
            Order.counted_toward_quota.is_(True),
        )
        .scalar()
        or 0
    )


def active_devices_count(db: Session, license_id: UUID) -> int:
    return db.query(Device).filter(Device.license_id == license_id, Device.is_active.is_(True)).count()


def resolve_license_client_code(db: Session, lic: License) -> str:
    """client_code de la empresa asociada a la licencia."""
    if lic.owner_user_id:
        owner = db.query(User).filter(User.id == lic.owner_user_id).first()
        if owner and owner.client_code:
            return owner.client_code
    return (lic.company_name or lic.code or "").upper().replace(" ", "")[:32]


def count_license_users(db: Session, lic: License, *, only_active: bool = False) -> int:
    """Cuentas (emails) ligadas a la empresa de la licencia."""
    code = resolve_license_client_code(db, lic)
    if not code:
        return 0
    q = db.query(func.count(User.id)).filter(
        User.client_code == code,
        User.role.in_(["employee", "company_admin", "client"]),
    )
    if only_active:
        q = q.filter(User.is_active.is_(True))
    return int(q.scalar() or 0)


def assert_license_user_seat(db: Session, lic: License) -> None:
    """Bloquea crear más correos del máximo permitido en la licencia."""
    max_users = int(getattr(lic, "max_users", 0) or 0)
    if max_users <= 0:
        return  # ilimitado
    current = count_license_users(db, lic, only_active=False)
    if current >= max_users:
        raise HTTPException(
            403,
            f"Esta licencia permite máximo {max_users} cuenta(s). "
            f"Ya hay {current} registradas. Contacta a FulfillPro para ampliar el plan.",
        )


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
        "max_users": int(getattr(lic, "max_users", 0) or 0),
        "users_count": count_license_users(db, lic),
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
        "analytics_enabled": bool(getattr(lic, "analytics_enabled", True)),
        "analytics_weeks_retention": int(getattr(lic, "analytics_weeks_retention", 12) or 12),
        "analytics_max_events_per_week": int(
            getattr(lic, "analytics_max_events_per_week", 50000) or 50000
        ),
        "analytics_storage_mb": int(getattr(lic, "analytics_storage_mb", 200) or 200),
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

    if template_name and template_name not in {"", "custom"}:
        base = resolve_template_payload(db, str(template_name).strip().lower())
        if not base:
            raise HTTPException(400, f"Plantilla desconocida: {template_name}")
        tpl_duration = base.pop("duration_days", None)
        # label vacío del form → usar default de plantilla
        if not (payload.get("label") or "").strip() and base.get("label"):
            payload["label"] = base["label"]
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
    features = dict(payload.get("features") or {})
    if template_name and template_name not in {"", "custom"}:
        features.setdefault("created_from_template", str(template_name).strip().lower())

    lic = License(
        code=code,
        label=payload.get("label", ""),
        type=payload.get("type", "standard"),
        company_name=payload.get("company_name", ""),
        owner_user_id=owner_id,
        max_devices=int(payload.get("max_devices") or 3),
        max_users=int(payload.get("max_users") if payload.get("max_users") is not None else 0),
        limit_uses=int(payload.get("limit_uses") or 0),
        daily_limit=int(payload.get("daily_limit") or 0),
        uses=0,
        expiry=expiry,
        active=True,
        analytics_enabled=bool(payload.get("analytics_enabled", True)),
        analytics_weeks_retention=int(payload.get("analytics_weeks_retention") or 12),
        analytics_max_events_per_week=int(payload.get("analytics_max_events_per_week") or 50000),
        analytics_storage_mb=int(payload.get("analytics_storage_mb") or 200),
        count_toward_global=payload.get("count_toward_global", True),
        enforce_daily_limit=payload.get("enforce_daily_limit", True),
        features=features,
        notes=payload.get("notes", ""),
        assigned_at=datetime.utcnow() if owner_id else None,
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)
    return lic


def _snapshot_license(lic: License) -> dict[str, Any]:
    return {
        "type": lic.type,
        "label": lic.label,
        "limit_uses": lic.limit_uses,
        "daily_limit": lic.daily_limit,
        "max_devices": lic.max_devices,
        "expiry": lic.expiry.isoformat() if lic.expiry else None,
        "uses": lic.uses,
        "active": lic.active,
        "count_toward_global": lic.count_toward_global,
        "enforce_daily_limit": lic.enforce_daily_limit,
    }


def _apply_expiry_policy(
    lic: License,
    *,
    expiry_policy: str = "keep",
    duration_days: Optional[int] = None,
    extend_days: Optional[int] = None,
    expiry: Optional[date] = None,
) -> None:
    """
    Políticas de vigencia al editar una licencia activa:
      - keep: no toca expiry
      - set_absolute: usa `expiry` tal cual
      - replace_from_today: expiry = hoy + duration_days
      - extend: suma días desde max(hoy, expiry actual)
        (usa extend_days o duration_days)
    """
    policy = (expiry_policy or "keep").strip().lower()
    if policy in {"", "keep", "none"}:
        if extend_days is not None and int(extend_days) != 0:
            policy = "extend"
        elif expiry is not None:
            policy = "set_absolute"
        elif duration_days is not None:
            policy = "replace_from_today"
        else:
            return

    today = date.today()
    if policy == "set_absolute":
        if expiry is None:
            raise HTTPException(400, "expiry_policy=set_absolute requiere fecha expiry.")
        lic.expiry = expiry
        return

    if policy == "replace_from_today":
        days = int(duration_days if duration_days is not None else extend_days or 0)
        if days <= 0:
            raise HTTPException(400, "duration_days debe ser > 0 para replace_from_today.")
        lic.expiry = today + timedelta(days=days)
        return

    if policy == "extend":
        days = int(extend_days if extend_days is not None else duration_days or 0)
        if days <= 0:
            raise HTTPException(400, "extend_days/duration_days debe ser > 0 para extend.")
        base = lic.expiry if lic.expiry and lic.expiry > today else today
        lic.expiry = base + timedelta(days=days)
        return

    raise HTTPException(
        400,
        f"expiry_policy no válida: {expiry_policy}. "
        "Usa keep | extend | replace_from_today | set_absolute.",
    )


def _append_plan_history(lic: License, entry: dict[str, Any]) -> None:
    feats = dict(lic.features or {})
    history = list(feats.get("plan_changes") or [])
    history.append(entry)
    # conservar últimas 50 entradas
    feats["plan_changes"] = history[-50:]
    lic.features = feats


def update_license(db: Session, lic: License, data: dict) -> License:
    """
    Actualiza una licencia activa sin recrearla.
    Conserva código, historial de usos (salvo reset_uses) y owner salvo que se envíe.
    """
    payload = dict(data)
    before = _snapshot_license(lic)

    apply_template = payload.pop("apply_template", None) or payload.pop("template", None)
    if apply_template and apply_template not in {"", "custom"}:
        base = resolve_template_payload(db, str(apply_template).strip().lower())
        if not base:
            raise HTTPException(400, f"Plantilla desconocida: {apply_template}")
        tpl_duration = base.pop("duration_days", None)
        for k, v in base.items():
            payload.setdefault(k, v)
        if payload.get("duration_days") is None and tpl_duration is not None:
            payload.setdefault("duration_days", tpl_duration)
        payload.setdefault("type", base.get("type") or apply_template)

    extend_days = payload.pop("extend_days", None)
    duration_days = payload.pop("duration_days", None)
    expiry_policy = payload.pop("expiry_policy", None)
    reset_uses = bool(payload.pop("reset_uses", False))
    append_note = payload.pop("append_note", None)

    for field in (
        "label",
        "type",
        "company_name",
        "max_devices",
        "max_users",
        "limit_uses",
        "daily_limit",
        "active",
        "count_toward_global",
        "enforce_daily_limit",
        "features",
        "notes",
        "analytics_enabled",
        "analytics_weeks_retention",
        "analytics_max_events_per_week",
        "analytics_storage_mb",
    ):
        if field in payload and payload[field] is not None:
            setattr(lic, field, payload[field])

    if "owner_user_id" in payload:
        lic.owner_user_id = payload["owner_user_id"]
        if payload["owner_user_id"]:
            lic.assigned_at = datetime.utcnow()

    # expiry: policy tiene prioridad; si solo mandan expiry, set_absolute
    exp_value = payload.get("expiry", None) if "expiry" in payload else None
    if expiry_policy or extend_days is not None or duration_days is not None or (
        "expiry" in payload and payload.get("expiry") is not None
    ):
        _apply_expiry_policy(
            lic,
            expiry_policy=expiry_policy
            or ("set_absolute" if exp_value is not None else "keep"),
            duration_days=int(duration_days) if duration_days is not None else None,
            extend_days=int(extend_days) if extend_days is not None else None,
            expiry=exp_value,
        )

    if reset_uses:
        lic.uses = 0

    if append_note and str(append_note).strip():
        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        line = f"[{stamp}] {str(append_note).strip()}"
        lic.notes = (f"{lic.notes}\n{line}" if lic.notes else line).strip()

    after = _snapshot_license(lic)
    if before != after:
        _append_plan_history(
            lic,
            {
                "at": datetime.utcnow().isoformat() + "Z",
                "action": "update",
                "before": before,
                "after": after,
                "template": apply_template,
            },
        )

    # Reactivar si venía vencida/inactiva y el cambio de plan lo pide
    if payload.get("active") is True:
        lic.active = True

    db.commit()
    db.refresh(lic)
    cache_delete(f"license:{lic.code}")
    return lic


def change_plan(db: Session, lic: License, data: dict) -> License:
    """
    Atajo semántico para upgrade/cambio de plan en caliente.
    Misma licencia y código; la empresa no cambia de credenciales.
    """
    payload = dict(data)
    template = payload.pop("template", None)
    apply_quotas = payload.pop("apply_template_quotas", True)
    if template and apply_quotas:
        payload["apply_template"] = template
    elif template and not apply_quotas:
        # solo marca el type del template si no hay type explícito
        payload.setdefault("type", template)

    # Defaults de política de tiempo al cambiar de plan
    if payload.get("expiry_policy") is None:
        if payload.get("extend_days") is not None:
            payload["expiry_policy"] = "extend"
        elif payload.get("duration_days") is not None:
            # Upgrade típico mensual→anual: nuevo ciclo desde hoy
            payload["expiry_policy"] = "replace_from_today"
        elif payload.get("expiry") is not None:
            payload["expiry_policy"] = "set_absolute"
        else:
            # Si hay template con duración implícita vía apply_template
            if template and apply_quotas:
                resolved = resolve_template_payload(db, str(template).strip().lower())
                if resolved and resolved.get("duration_days"):
                    payload.setdefault("duration_days", resolved["duration_days"])
                payload["expiry_policy"] = "replace_from_today"
            else:
                payload["expiry_policy"] = "keep"

    if payload.get("active") is None:
        payload["active"] = True

    reason = payload.get("append_note") or ""
    if template and not reason:
        payload["append_note"] = f"Cambio de plan → plantilla '{template}'"
    elif not reason and (payload.get("duration_days") or payload.get("extend_days")):
        payload["append_note"] = "Ajuste de vigencia / plan sin recrear licencia"

    before_type = lic.type
    lic = update_license(db, lic, payload)
    # Marcar última acción como change_plan en history
    feats = dict(lic.features or {})
    hist = list(feats.get("plan_changes") or [])
    if hist:
        hist[-1]["action"] = "change_plan"
        hist[-1]["from_type"] = before_type
        hist[-1]["to_type"] = lic.type
        feats["plan_changes"] = hist
        lic.features = feats
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
