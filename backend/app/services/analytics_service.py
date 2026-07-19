from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.models.analytics import (
    AnalyticsConsolidation,
    AnalyticsSaleEvent,
    AnalyticsWeek,
)
from backend.app.models.license import License
from backend.app.models.user import User
from backend.app.services import storage_service
from backend.app.services.audit_service import log_access


WEEK_DAYS = 7


def _normalize_product(name: str) -> str:
    return " ".join(str(name or "").strip().split())


def _normalize_var(var: str) -> str:
    v = str(var or "").strip()
    if not v or v.lower() in {"nan", "none", "null", "-"}:
        return ""
    return v


def make_dedup_key(row: dict[str, Any]) -> str:
    """
    Clave estable para no contar dos veces la misma orden/línea
    si reaparece en otro Excel de la misma semana.
    Prioridad: guía + producto + variación; si no hay guía, id + producto + variación.
    """
    guia = str(row.get("guia") or "").strip()
    oid = str(row.get("id") or "").strip()
    prod = _normalize_product(row.get("producto", "")).upper()
    var = _normalize_var(row.get("variacion")).upper()
    cant = str(row.get("cantidad") or 1)
    base = f"{guia}|{oid}|{prod}|{var}|{cant}"
    # Hash corto + prefijo legible para unicidad y debug
    h = hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]
    ref = guia or oid or "NOREF"
    return f"{ref}:{h}"


def analytics_limits(lic: Optional[License]) -> dict[str, int | bool]:
    if not lic:
        return {
            "enabled": True,
            "weeks_retention": 12,
            "max_events_per_week": 50000,
            "storage_mb": 200,
        }
    features = lic.features or {}
    enabled = lic.analytics_enabled
    if "analytics_enabled" in features:
        enabled = bool(features.get("analytics_enabled"))
    return {
        "enabled": bool(enabled),
        "weeks_retention": int(
            features.get("analytics_weeks_retention")
            or lic.analytics_weeks_retention
            or 12
        ),
        "max_events_per_week": int(
            features.get("analytics_max_events_per_week")
            or lic.analytics_max_events_per_week
            or 50000
        ),
        "storage_mb": int(
            features.get("analytics_storage_mb") or lic.analytics_storage_mb or 200
        ),
    }


def _refresh_week_status(week: AnalyticsWeek) -> None:
    if week.status == "open" and week.ends_at and datetime.utcnow() >= week.ends_at:
        week.status = "ended"


def get_or_create_open_week(
    db: Session,
    *,
    client_code: str,
    company_name: str,
    license_id: Optional[UUID],
    limits: dict,
) -> AnalyticsWeek:
    """
    Obtiene la semana abierta. Si la vigente ya venció, la marca ended.
    Solo crea una nueva cuando hay ingest (primera subida tras reinicio).
    """
    week = (
        db.query(AnalyticsWeek)
        .filter(AnalyticsWeek.client_code == client_code, AnalyticsWeek.status == "open")
        .order_by(AnalyticsWeek.started_at.desc())
        .first()
    )
    if week:
        _refresh_week_status(week)
        if week.status == "open":
            db.commit()
            return week
        db.commit()

    now = datetime.utcnow()
    week = AnalyticsWeek(
        client_code=client_code,
        company_name=company_name or client_code,
        license_id=license_id,
        started_at=now,
        ends_at=now + timedelta(days=WEEK_DAYS),
        status="open",
    )
    db.add(week)
    db.commit()
    db.refresh(week)
    prune_old_weeks(db, client_code=client_code, retention=int(limits["weeks_retention"]))
    return week


def get_current_week(db: Session, client_code: str) -> Optional[AnalyticsWeek]:
    week = (
        db.query(AnalyticsWeek)
        .filter(AnalyticsWeek.client_code == client_code)
        .order_by(AnalyticsWeek.started_at.desc())
        .first()
    )
    if week and week.status == "open":
        _refresh_week_status(week)
        db.commit()
        db.refresh(week)
    return week


def prune_old_weeks(db: Session, *, client_code: str, retention: int) -> int:
    """Elimina semanas más allá de la retención (libera storage de analítica)."""
    if retention <= 0:
        return 0
    weeks = (
        db.query(AnalyticsWeek)
        .filter(AnalyticsWeek.client_code == client_code)
        .order_by(AnalyticsWeek.started_at.desc())
        .all()
    )
    removed = 0
    for w in weeks[retention:]:
        # borrar archivo de consolidado si existe
        if w.consolidation and w.consolidation.relative_path:
            try:
                p = storage_service.absolute_from_relative(w.consolidation.relative_path)
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        db.delete(w)
        removed += 1
    if removed:
        db.commit()
    return removed


def estimate_analytics_storage_bytes(db: Session, client_code: str) -> int:
    total = 0
    cons = (
        db.query(AnalyticsConsolidation)
        .join(AnalyticsWeek)
        .filter(AnalyticsWeek.client_code == client_code)
        .all()
    )
    for c in cons:
        total += int(c.size_bytes or 0)
    # aproximación: ~200 bytes por evento
    events = (
        db.query(func.count(AnalyticsSaleEvent.id))
        .join(AnalyticsWeek)
        .filter(AnalyticsWeek.client_code == client_code)
        .scalar()
        or 0
    )
    total += int(events) * 200
    return total


def ingest_order_rows(
    db: Session,
    *,
    user: User,
    lic: Optional[License],
    source_order_id: UUID,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ingiere filas del Excel en la semana actual con deduplicación."""
    limits = analytics_limits(lic)
    if not limits["enabled"]:
        return {"skipped": True, "reason": "analytics_disabled"}

    client_code = user.client_code or "DEFAULT"
    company_name = user.company_name or client_code

    # Si la semana abierta ya venció, no sumar hasta que se cree una nueva al subir
    open_week = (
        db.query(AnalyticsWeek)
        .filter(AnalyticsWeek.client_code == client_code, AnalyticsWeek.status == "open")
        .order_by(AnalyticsWeek.started_at.desc())
        .first()
    )
    if open_week:
        _refresh_week_status(open_week)
        db.commit()
        if open_week.status != "open":
            # Periodo cerrado: se reinicia contador con esta subida (nueva semana)
            open_week = None

    if not open_week:
        # Comprobar storage antes de abrir semana nueva
        used = estimate_analytics_storage_bytes(db, client_code)
        max_bytes = int(limits["storage_mb"]) * 1024 * 1024
        if max_bytes > 0 and used >= max_bytes:
            raise HTTPException(
                403,
                f"Límite de almacenamiento de analítica alcanzado "
                f"({limits['storage_mb']} MB). Genera/consolida y contacta soporte o amplia el plan.",
            )
        open_week = get_or_create_open_week(
            db,
            client_code=client_code,
            company_name=company_name,
            license_id=lic.id if lic else None,
            limits=limits,
        )

    max_events = int(limits["max_events_per_week"])
    added = 0
    skipped_dup = 0
    skipped_cap = 0
    units_added = 0
    # Dedup también dentro del mismo archivo (mismo batch)
    seen_batch: set[str] = set()

    for row in rows:
        prod = _normalize_product(row.get("producto", ""))
        if not prod:
            continue
        try:
            qty = int(row.get("cantidad") or 1)
        except Exception:
            qty = 1
        if qty < 1:
            qty = 1

        dedup = make_dedup_key(row)
        if dedup in seen_batch:
            skipped_dup += 1
            continue
        exists = (
            db.query(AnalyticsSaleEvent.id)
            .filter(
                AnalyticsSaleEvent.week_id == open_week.id,
                AnalyticsSaleEvent.dedup_key == dedup,
            )
            .first()
        )
        if exists:
            skipped_dup += 1
            continue

        if max_events > 0 and (open_week.events_count or 0) + added >= max_events:
            skipped_cap += 1
            continue

        ev = AnalyticsSaleEvent(
            week_id=open_week.id,
            dedup_key=dedup,
            order_ref=str(row.get("id") or "")[:128],
            guia=str(row.get("guia") or "")[:128],
            product_name=prod[:512],
            variation=_normalize_var(row.get("variacion"))[:255],
            quantity=qty,
            source_order_id=source_order_id,
        )
        db.add(ev)
        seen_batch.add(dedup)
        added += 1
        units_added += qty

    open_week.events_count = (open_week.events_count or 0) + added
    open_week.unique_lines = open_week.events_count
    open_week.total_units = (open_week.total_units or 0) + units_added
    open_week.files_ingested = (open_week.files_ingested or 0) + 1
    db.flush()
    # guías distintas; si no hay guía, contar order_ref
    n_guias = (
        db.query(func.count(func.distinct(AnalyticsSaleEvent.guia)))
        .filter(
            AnalyticsSaleEvent.week_id == open_week.id,
            AnalyticsSaleEvent.guia != "",
        )
        .scalar()
        or 0
    )
    if n_guias:
        open_week.unique_orders = int(n_guias)
    else:
        open_week.unique_orders = int(
            db.query(func.count(func.distinct(AnalyticsSaleEvent.order_ref)))
            .filter(
                AnalyticsSaleEvent.week_id == open_week.id,
                AnalyticsSaleEvent.order_ref != "",
            )
            .scalar()
            or open_week.events_count
            or 0
        )
    db.commit()
    db.refresh(open_week)

    return {
        "skipped": False,
        "week_id": str(open_week.id),
        "added": added,
        "skipped_duplicates": skipped_dup,
        "skipped_cap": skipped_cap,
        "units_added": units_added,
        "week_status": open_week.status,
        "ends_at": open_week.ends_at.isoformat() if open_week.ends_at else None,
    }


def top_products(db: Session, week_id: UUID, limit: int = 20) -> list[dict]:
    rows = (
        db.query(
            AnalyticsSaleEvent.product_name,
            AnalyticsSaleEvent.variation,
            func.sum(AnalyticsSaleEvent.quantity).label("units"),
            func.count(AnalyticsSaleEvent.id).label("lines"),
        )
        .filter(AnalyticsSaleEvent.week_id == week_id)
        .group_by(AnalyticsSaleEvent.product_name, AnalyticsSaleEvent.variation)
        .order_by(func.sum(AnalyticsSaleEvent.quantity).desc())
        .limit(limit)
        .all()
    )
    out = []
    for name, var, units, lines in rows:
        label = f"{name} [{var}]" if var else name
        out.append(
            {
                "product_name": name,
                "variation": var or "",
                "label": label,
                "units": int(units or 0),
                "lines": int(lines or 0),
            }
        )
    return out


def week_payload(db: Session, week: AnalyticsWeek, *, include_chart: bool = True) -> dict:
    _refresh_week_status(week)
    db.commit()
    now = datetime.utcnow()
    remaining = None
    if week.ends_at:
        remaining = max(int((week.ends_at - now).total_seconds()), 0)
    # si open pero ya pasó ends_at, marcar ended
    if week.status == "open" and week.ends_at and now >= week.ends_at:
        week.status = "ended"
        db.commit()

    elapsed_days = 0.0
    if week.started_at:
        elapsed_days = max((now - week.started_at).total_seconds() / 86400.0, 0.0)

    period_complete = week.status in ("ended", "consolidated") or (
        week.ends_at is not None and now >= week.ends_at
    )
    is_early = week.status == "open" and not period_complete
    # Admin puede consolidar en cualquier momento (normal o forzado)
    can_consolidate = week.status != "consolidated" and (week.events_count or 0) >= 0

    data = {
        "id": str(week.id),
        "client_code": week.client_code,
        "company_name": week.company_name,
        "status": week.status,
        "started_at": week.started_at.isoformat() if week.started_at else None,
        "ends_at": week.ends_at.isoformat() if week.ends_at else None,
        "seconds_remaining": remaining,
        "days_elapsed": round(min(elapsed_days, float(WEEK_DAYS) + 30), 2),
        "days_total": WEEK_DAYS,
        "unique_orders": week.unique_orders or 0,
        "unique_lines": week.unique_lines or 0,
        "total_units": week.total_units or 0,
        "files_ingested": week.files_ingested or 0,
        "events_count": week.events_count or 0,
        "can_consolidate": can_consolidate,
        "is_early": is_early,
        "period_complete": period_complete,
        "consolidated_at": week.consolidated_at.isoformat() if week.consolidated_at else None,
        "has_consolidation": bool(week.consolidation and week.status == "consolidated"),
        "early_consolidation": bool(
            week.consolidation
            and (week.consolidation.snapshot or {}).get("early_consolidation")
        ),
        "days_length": (week.consolidation.snapshot or {}).get("days_length")
        if week.consolidation
        else None,
    }
    if include_chart:
        tops = top_products(db, week.id, limit=15)
        data["top_products"] = tops
        data["chart"] = {
            "labels": [t["label"][:40] for t in tops],
            "units": [t["units"] for t in tops],
            "lines": [t["lines"] for t in tops],
        }
    return data


def list_weeks(db: Session, client_code: str, limit: int = 20) -> list[dict]:
    weeks = (
        db.query(AnalyticsWeek)
        .filter(AnalyticsWeek.client_code == client_code)
        .order_by(AnalyticsWeek.started_at.desc())
        .limit(limit)
        .all()
    )
    return [week_payload(db, w, include_chart=False) for w in weeks]


def consolidate_week(
    db: Session,
    *,
    user: User,
    week_id: UUID,
    force: bool = False,
) -> dict:
    week = db.query(AnalyticsWeek).filter(AnalyticsWeek.id == week_id).first()
    if not week:
        raise HTTPException(404, "Semana no encontrada.")
    if week.client_code != user.client_code and user.role != "admin":
        raise HTTPException(403, "No pertenece a tu empresa.")
    if user.role not in ("company_admin", "admin"):
        raise HTTPException(403, "Solo el administrador de la empresa puede consolidar.")

    _refresh_week_status(week)
    now = datetime.utcnow()
    period_complete = week.status in ("ended", "consolidated") or (
        week.ends_at is not None and now >= week.ends_at
    )
    early = week.status == "open" and not period_complete

    if early and not force:
        raise HTTPException(
            400,
            "La semana aún está en curso. Usa 'Forzar consolidado' (force=true) si deseas "
            "cerrar el ciclo de forma anticipada. El documento indicará la duración real "
            "y una advertencia por consolidado temprano.",
        )

    if week.status == "consolidated" and week.consolidation:
        snap = week.consolidation.snapshot or {}
        return {
            "ok": True,
            "already": True,
            "early": bool(snap.get("early_consolidation")),
            "days_length": snap.get("days_length"),
            "week": week_payload(db, week),
            "snapshot": snap,
            "download": {
                "pdf": f"/api/analytics/weeks/{week.id}/download?format=pdf",
                "json": f"/api/analytics/weeks/{week.id}/download?format=json",
            },
        }

    # Longitud real del periodo consolidado (desde inicio hasta ahora)
    if week.started_at:
        length_seconds = max((now - week.started_at).total_seconds(), 0)
    else:
        length_seconds = 0
    days_length = round(length_seconds / 86400.0, 2)
    # al menos mostrar fracción de día si hay actividad el mismo día
    if days_length < 0.01 and (week.events_count or 0) > 0:
        days_length = 0.01

    planned_end = week.ends_at
    early_warning = (
        "ADVERTENCIA: Este consolidado se generó de forma anticipada (antes de completar "
        f"los {WEEK_DAYS} días del ciclo). Los datos no están pensados para mostrarse "
        "antes de tiempo y pueden generar incoherencias con análisis posteriores o con "
        "comparativas entre semanas completas. Úselo solo como vista parcial del periodo."
    )

    tops = top_products(db, week.id, limit=50)
    top5 = tops[:5]
    from backend.app.services.analytics_pdf import build_consolidation_pdf, product_daily_flow

    flow = product_daily_flow(
        db,
        week.id,
        top5,
        started_at=week.started_at,
        until=now,
    )
    snapshot = {
        "title": "Consolidado de productos más vendidos",
        "company_name": week.company_name,
        "client_code": week.client_code,
        "started_at": week.started_at.isoformat() if week.started_at else None,
        "ends_at": planned_end.isoformat() if planned_end else None,
        "consolidated_at": now.isoformat(),
        "period": {
            "planned_days": WEEK_DAYS,
            "actual_days_length": days_length,
            "actual_hours": round(length_seconds / 3600.0, 1),
            "early_consolidation": early,
            "period_complete": period_complete and not early,
            "label": (
                f"Consolidado temprano · {days_length} día(s) de {WEEK_DAYS}"
                if early
                else f"Consolidado completo · {days_length} día(s)"
            ),
        },
        "early_consolidation": early,
        "days_length": days_length,
        "warnings": [early_warning] if early else [],
        "unique_orders": week.unique_orders,
        "unique_lines": week.unique_lines,
        "total_units": week.total_units,
        "files_ingested": week.files_ingested,
        "top_products": tops,
        "top5": top5,
        "flow": flow,
        "chart": {
            "labels": [t["label"][:40] for t in tops[:15]],
            "units": [t["units"] for t in tops[:15]],
            "lines": [t["lines"] for t in tops[:15]],
        },
        "generated_at": now.isoformat(),
        "generated_by": str(user.id),
    }

    from pathlib import Path

    from backend.app.config import get_settings

    # storage/{client}/analytics/{YYYY}/{MM}/{week_id}/
    when = week.started_at or datetime.utcnow()
    analytics_dir = (
        Path(get_settings().storage_root)
        / storage_service._safe_segment(week.client_code or "analytics")
        / "analytics"
        / f"{when.year:04d}"
        / f"{when.month:02d}"
        / str(week.id)
    )
    analytics_dir.mkdir(parents=True, exist_ok=True)

    # PDF principal
    pdf_bytes = build_consolidation_pdf(week=week, snapshot=snapshot, flow=flow)
    pdf_path = analytics_dir / "consolidado.pdf"
    pdf_path.write_bytes(pdf_bytes)

    # JSON de respaldo (sin imágenes)
    snap_path = analytics_dir / "consolidado.json"
    raw = json.dumps(snapshot, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    snap_path.write_bytes(raw)

    rel_pdf = storage_service.relative_to_storage(pdf_path)
    rel_json = storage_service.relative_to_storage(snap_path)
    size_total = len(pdf_bytes) + len(raw)
    snapshot["files"] = {
        "pdf": rel_pdf,
        "json": rel_json,
    }

    cons = week.consolidation
    if not cons:
        cons = AnalyticsConsolidation(week_id=week.id)
        db.add(cons)
    cons.generated_at = now
    cons.generated_by = user.id
    cons.snapshot = snapshot
    cons.relative_path = rel_pdf
    cons.size_bytes = size_total

    # Cerrar ciclo: el contador se reinicia con la próxima subida
    week.status = "consolidated"
    week.consolidated_at = now
    week.consolidated_by = user.id
    # Si fue temprano, acortar ends_at al momento del cierre para claridad histórica
    if early:
        week.ends_at = now
    db.commit()
    db.refresh(week)

    log_access(
        db,
        event_type="analytics_consolidate",
        detail=(
            f"Consolidado {'temprano' if early else 'completo'} semana {week.id} · "
            f"{days_length}d · {week.total_units} uds"
        ),
        user_id=user.id,
    )
    return {
        "ok": True,
        "already": False,
        "early": early,
        "days_length": days_length,
        "week": week_payload(db, week),
        "snapshot": snapshot,
        "download": {
            "pdf": f"/api/analytics/weeks/{week.id}/download?format=pdf",
            "json": f"/api/analytics/weeks/{week.id}/download?format=json",
        },
    }


def resolve_consolidation_file(week: AnalyticsWeek, fmt: str = "pdf") -> tuple[str, str, bytes]:
    """
    Devuelve (filename, media_type, content) del consolidado.
    Formatos: pdf (principal), json (respaldo).
    """
    fmt = (fmt or "pdf").lower().strip()
    if fmt in ("txt", "text"):
        fmt = "pdf"  # legacy: ya no se sirve txt
    if fmt not in ("pdf", "json"):
        raise HTTPException(400, "Formato no válido. Usa pdf o json.")

    cons = week.consolidation
    if not cons or week.status != "consolidated":
        raise HTTPException(404, "Esta semana no tiene consolidado generado.")

    snap = cons.snapshot or {}
    files = snap.get("files") or {}
    base_name = f"Consolidado_{week.client_code or 'empresa'}_{str(week.id)[:8]}"

    if fmt == "pdf":
        rel = files.get("pdf") or ""
        if not rel and cons.relative_path and str(cons.relative_path).endswith(".pdf"):
            rel = cons.relative_path
        filename = f"{base_name}.pdf"
        media = "application/pdf"
        if rel:
            path = storage_service.absolute_from_relative(rel)
            if path.exists():
                return filename, media, path.read_bytes()
        # Regenerar PDF si falta el archivo en disco
        from backend.app.database import SessionLocal
        from backend.app.services.analytics_pdf import (
            build_consolidation_pdf,
            product_daily_flow,
        )

        flow = snap.get("flow")
        if not flow or not flow.get("series"):
            # intentar reconstruir flujo desde eventos de la semana
            db = SessionLocal()
            try:
                tops = snap.get("top5") or (snap.get("top_products") or [])[:5]
                flow = product_daily_flow(
                    db,
                    week.id,
                    tops,
                    started_at=week.started_at,
                    until=week.consolidated_at or datetime.utcnow(),
                )
            finally:
                db.close()
        pdf_bytes = build_consolidation_pdf(
            week=week, snapshot=snap, flow=flow or {"days": [], "series": []}
        )
        return filename, media, pdf_bytes

    # json
    rel = files.get("json") or ""
    filename = f"{base_name}.json"
    media = "application/json; charset=utf-8"
    if rel and storage_service.absolute_from_relative(rel).exists():
        return filename, media, storage_service.absolute_from_relative(rel).read_bytes()
    slim = dict(snap)
    if "files" in slim and isinstance(slim["files"], dict):
        slim["files"] = {k: v for k, v in slim["files"].items() if k != "report_text"}
    return filename, media, json.dumps(slim, indent=2, ensure_ascii=False, default=str).encode("utf-8")
