"""
Consolidado diario de guías / despachos.

- Se alimenta en cada proceso de Excel.
- Tras 28 días desde la fecha de despacho el día queda liberado para vendedores.
- Descarga Excel con estado estimado de cada guía.
"""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy.orm import Session

from backend.app.models.dispatch import DispatchDay, DispatchGuia
from backend.app.models.license import License
from backend.app.models.user import User
from backend.app.services.excel.reader import parse_date

HOLD_DAYS = 28


def _status_for_age(days: int) -> str:
    if days < 0:
        return "PROGRAMADO"
    if days <= 3:
        return "EN_TRANSITO"
    if days <= 10:
        return "EN_REPARTO"
    if days <= 27:
        return "ENTREGA_ESPERADA"
    return "CIERRE_28D"


def ensure_day(
    db: Session,
    *,
    client_code: str,
    company_name: str,
    license_id: Optional[UUID],
    dispatch_date: date,
) -> DispatchDay:
    day = (
        db.query(DispatchDay)
        .filter(
            DispatchDay.client_code == client_code,
            DispatchDay.dispatch_date == dispatch_date,
        )
        .first()
    )
    if day:
        return day
    day = DispatchDay(
        client_code=client_code,
        company_name=company_name or "",
        license_id=license_id,
        dispatch_date=dispatch_date,
    )
    db.add(day)
    db.flush()
    return day


def ingest_dispatch_rows(
    db: Session,
    *,
    user: User,
    lic: Optional[License],
    source_order_id: UUID,
    rows: list[dict[str, Any]],
    process_date: Optional[date] = None,
) -> dict[str, Any]:
    """Agrupa por guía y acumula en el día de despacho."""
    process_date = process_date or date.today()
    client_code = user.client_code or "DEFAULT"
    company_name = user.company_name or (lic.company_name if lic else "") or client_code
    license_id = lic.id if lic else None

    by_guia: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g = str(r.get("guia") or "").strip()
        if not g:
            continue
        by_guia[g].append(r)

    if not by_guia:
        return {"added": 0, "days": 0}

    days_touched: set[date] = set()
    added = 0
    for guia, items in by_guia.items():
        # Fecha de la guía o día de proceso
        fechas = [parse_date(it.get("fechaGuia")) for it in items]
        fechas = [f for f in fechas if f]
        ddate = min(fechas) if fechas else process_date
        days_touched.add(ddate)
        day = ensure_day(
            db,
            client_code=client_code,
            company_name=company_name,
            license_id=license_id,
            dispatch_date=ddate,
        )
        existing = (
            db.query(DispatchGuia)
            .filter(DispatchGuia.day_id == day.id, DispatchGuia.guia == guia)
            .first()
        )
        products = []
        qty = 0
        value = 0.0
        city = ""
        carrier = ""
        order_ref = ""
        for it in items:
            products.append(
                f"{it.get('producto') or ''}{' [' + str(it.get('variacion')) + ']' if it.get('variacion') else ''} x{it.get('cantidad') or 1}"
            )
            qty += int(it.get("cantidad") or 0)
            value += float(it.get("valor") or 0)
            if not city:
                city = str(it.get("ciudad") or "").strip()
            if not carrier:
                carrier = str(it.get("transportadora") or "").strip()
            if not order_ref:
                order_ref = str(it.get("id") or "").strip()

        age = (date.today() - ddate).days
        status = _status_for_age(age)
        summary = " | ".join(products)[:2000]

        if existing:
            existing.product_summary = summary
            existing.quantity = qty
            existing.value = value
            existing.city = city or existing.city
            existing.carrier = carrier or existing.carrier
            existing.fecha_guia = ddate
            existing.status = status
            existing.order_ref = order_ref or existing.order_ref
            existing.source_order_id = source_order_id
        else:
            db.add(
                DispatchGuia(
                    day_id=day.id,
                    guia=guia,
                    order_ref=order_ref,
                    product_summary=summary,
                    quantity=qty,
                    value=value,
                    city=city,
                    carrier=carrier,
                    fecha_guia=ddate,
                    status=status,
                    source_order_id=source_order_id,
                )
            )
            added += 1

    # Recalcular contadores de días tocados
    for d in days_touched:
        day = (
            db.query(DispatchDay)
            .filter(DispatchDay.client_code == client_code, DispatchDay.dispatch_date == d)
            .first()
        )
        if not day:
            continue
        lines = db.query(DispatchGuia).filter(DispatchGuia.day_id == day.id).all()
        day.guias_count = len(lines)
        day.orders_count = len({(ln.order_ref or ln.guia) for ln in lines})
        day.total_value = float(sum(ln.value or 0 for ln in lines))
        day.files_ingested = int(day.files_ingested or 0) + 1
        day.updated_at = datetime.utcnow()
        # Liberar si ya pasaron 28 días
        if date.today() >= day.dispatch_date + timedelta(days=HOLD_DAYS):
            if not day.released:
                day.released = True
                day.released_at = datetime.utcnow()

    db.commit()
    return {"added": added, "days": len(days_touched), "hold_days": HOLD_DAYS}


def refresh_release_flags(db: Session, client_code: str) -> int:
    """Marca como liberados los días con antigüedad >= 28."""
    cutoff = date.today() - timedelta(days=HOLD_DAYS)
    rows = (
        db.query(DispatchDay)
        .filter(
            DispatchDay.client_code == client_code,
            DispatchDay.released.is_(False),
            DispatchDay.dispatch_date <= cutoff,
        )
        .all()
    )
    n = 0
    for day in rows:
        day.released = True
        day.released_at = datetime.utcnow()
        # Actualizar estados de guías
        for ln in day.lines:
            if ln.fecha_guia:
                ln.status = _status_for_age((date.today() - ln.fecha_guia).days)
        n += 1
    if n:
        db.commit()
    return n


def list_days(db: Session, user: User, *, released_only: bool = False) -> list[dict]:
    if not user.client_code and user.role != "admin":
        return []
    refresh_release_flags(db, user.client_code or "")
    q = db.query(DispatchDay)
    if user.role != "admin":
        q = q.filter(DispatchDay.client_code == user.client_code)
    if released_only:
        q = q.filter(DispatchDay.released.is_(True))
    days = q.order_by(DispatchDay.dispatch_date.desc()).limit(90).all()
    today = date.today()
    out = []
    for d in days:
        unlock_on = d.dispatch_date + timedelta(days=HOLD_DAYS)
        days_left = (unlock_on - today).days
        out.append(
            {
                "id": str(d.id),
                "dispatch_date": d.dispatch_date.isoformat(),
                "company_name": d.company_name,
                "guias_count": d.guias_count,
                "orders_count": d.orders_count,
                "total_value": d.total_value,
                "released": bool(d.released) or days_left <= 0,
                "unlock_on": unlock_on.isoformat(),
                "days_until_unlock": max(0, days_left),
            }
        )
    return out


def get_day(db: Session, user: User, day_id: UUID) -> DispatchDay:
    day = db.query(DispatchDay).filter(DispatchDay.id == day_id).first()
    if not day:
        raise HTTPException(404, "Día de despacho no encontrado.")
    if user.role != "admin" and day.client_code != user.client_code:
        raise HTTPException(403, "Sin acceso a este consolidado.")
    refresh_release_flags(db, day.client_code)
    db.refresh(day)
    return day


def day_payload(db: Session, user: User, day_id: UUID) -> dict:
    day = get_day(db, user, day_id)
    today = date.today()
    unlock_on = day.dispatch_date + timedelta(days=HOLD_DAYS)
    released = bool(day.released) or today >= unlock_on
    lines = []
    for ln in day.lines:
        age = (today - (ln.fecha_guia or day.dispatch_date)).days
        st = _status_for_age(age)
        if ln.status != st:
            ln.status = st
        lines.append(
            {
                "guia": ln.guia,
                "order_ref": ln.order_ref,
                "product_summary": ln.product_summary,
                "quantity": ln.quantity,
                "value": ln.value,
                "city": ln.city,
                "carrier": ln.carrier,
                "fecha_guia": ln.fecha_guia.isoformat() if ln.fecha_guia else None,
                "status": ln.status,
                "days_since_dispatch": age,
            }
        )
    db.commit()
    return {
        "id": str(day.id),
        "dispatch_date": day.dispatch_date.isoformat(),
        "released": released,
        "unlock_on": unlock_on.isoformat(),
        "days_until_unlock": max(0, (unlock_on - today).days),
        "guias_count": day.guias_count,
        "orders_count": day.orders_count,
        "total_value": day.total_value,
        "lines": lines,
        "hold_days": HOLD_DAYS,
        "message": (
            "Disponible para vendedores."
            if released
            else f"Se liberará el {unlock_on.isoformat()} ({HOLD_DAYS} días tras el despacho)."
        ),
    }


def build_day_excel(db: Session, user: User, day_id: UUID) -> tuple[bytes, str]:
    payload = day_payload(db, user, day_id)
    if not payload["released"] and user.role != "admin":
        raise HTTPException(
            403,
            f"Este consolidado se libera el {payload['unlock_on']} "
            f"({payload['days_until_unlock']} día(s) restantes).",
        )
    wb = Workbook()
    ws = wb.active
    ws.title = "Guías 28d"
    ws.append(
        [
            "GUIA",
            "ID ORDEN",
            "PRODUCTOS",
            "CANTIDAD",
            "VALOR",
            "CIUDAD",
            "TRANSPORTADORA",
            "FECHA GUIA",
            "DIAS DESDE DESPACHO",
            "ESTADO",
        ]
    )
    for ln in payload["lines"]:
        ws.append(
            [
                ln["guia"],
                ln["order_ref"],
                ln["product_summary"],
                ln["quantity"],
                ln["value"],
                ln["city"],
                ln["carrier"],
                ln["fecha_guia"] or "",
                ln["days_since_dispatch"],
                ln["status"],
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    name = f"consolidado_guias_{payload['dispatch_date']}.xlsx"
    return buf.getvalue(), name


def stats_summary(db: Session, user: User, *, days: int = 90) -> dict:
    """Estadísticas descriptivas: top productos en $, ciudades, transportadoras."""
    if not user.client_code and user.role != "admin":
        return {"top_products_money": [], "cities": [], "carriers": []}
    since = date.today() - timedelta(days=days)
    q = db.query(DispatchGuia).join(DispatchDay)
    if user.role != "admin":
        q = q.filter(DispatchDay.client_code == user.client_code)
    q = q.filter(DispatchDay.dispatch_date >= since)
    lines = q.all()

    by_prod: dict[str, float] = defaultdict(float)
    by_city: dict[str, int] = defaultdict(int)
    by_carrier: dict[str, int] = defaultdict(int)
    for ln in lines:
        # reparto proporcional simple del valor por nombre de producto en summary
        name = (ln.product_summary or "SIN PRODUCTO").split(" | ")[0].split(" x")[0].strip() or "SIN PRODUCTO"
        by_prod[name] += float(ln.value or 0)
        if ln.city:
            by_city[ln.city.strip().upper()] += 1
        if ln.carrier:
            by_carrier[ln.carrier.strip().upper()] += 1
        elif not ln.city and not ln.carrier:
            pass

    top_products = sorted(by_prod.items(), key=lambda x: -x[1])[:15]
    cities = sorted(by_city.items(), key=lambda x: -x[1])[:15]
    carriers = sorted(by_carrier.items(), key=lambda x: -x[1])[:15]
    return {
        "period_days": days,
        "guias_total": len(lines),
        "top_products_money": [{"name": n, "value": round(v, 2)} for n, v in top_products],
        "cities": [{"name": n, "orders": c} for n, c in cities],
        "carriers": [{"name": n, "orders": c} for n, c in carriers],
        "note": (
            "Ciudad y transportadora se capturan si el Excel de entrada trae esas columnas. "
            "Si no aparecen, rellena CIUDAD y TRANSPORTADORA en el archivo de órdenes."
        ),
    }
