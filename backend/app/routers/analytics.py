from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.dependencies import get_current_user
from backend.app.models.analytics import AnalyticsWeek
from backend.app.models.user import User
from backend.app.services import analytics_service, license_service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _require_company_staff(user: User) -> User:
    if user.role not in ("company_admin", "admin", "employee", "client"):
        raise HTTPException(403, "Sin acceso a analítica.")
    if user.role == "admin" and not user.client_code:
        raise HTTPException(400, "Cuenta plataforma sin empresa de contexto.")
    if not user.client_code and user.role != "admin":
        raise HTTPException(400, "Tu cuenta no tiene código de empresa.")
    return user


def _require_company_admin(user: User) -> User:
    if user.role not in ("company_admin", "admin"):
        raise HTTPException(403, "Solo el administrador de la empresa puede realizar esta acción.")
    return user


@router.get("/current")
def current_week(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Semana actual (tiempo real) + gráficas de productos más vendidos."""
    _require_company_staff(user)
    lic = license_service.get_user_license(db, user)
    limits = analytics_service.analytics_limits(lic)
    if not limits["enabled"]:
        return {"ok": False, "message": "Analítica no incluida en el plan.", "limits": limits}

    week = analytics_service.get_current_week(db, user.client_code)
    used = analytics_service.estimate_analytics_storage_bytes(db, user.client_code)
    storage = {
        "used_bytes": used,
        "used_mb": round(used / (1024 * 1024), 2),
        "limit_mb": limits["storage_mb"],
    }

    if not week:
        return {
            "ok": True,
            "week": None,
            "message": "Aún no hay semana de analítica. Se inicia al subir el primer Excel del ciclo.",
            "limits": limits,
            "storage": storage,
            "weeks": analytics_service.list_weeks(db, user.client_code, limit=8),
        }

    return {
        "ok": True,
        "week": analytics_service.week_payload(db, week, include_chart=True),
        "limits": limits,
        "storage": storage,
        "weeks": analytics_service.list_weeks(db, user.client_code, limit=8),
    }


@router.get("/weeks")
def list_weeks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_company_staff(user)
    return {"items": analytics_service.list_weeks(db, user.client_code, limit=24)}


@router.get("/weeks/{week_id}")
def week_detail(
    week_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_company_staff(user)
    week = db.query(AnalyticsWeek).filter(AnalyticsWeek.id == week_id).first()
    if not week:
        raise HTTPException(404, "Semana no encontrada.")
    if week.client_code != user.client_code and user.role != "admin":
        raise HTTPException(403, "Sin acceso.")
    payload = analytics_service.week_payload(db, week, include_chart=True)
    snapshot = None
    if week.consolidation:
        snapshot = week.consolidation.snapshot or {}
    return {
        "ok": True,
        "week": payload,
        "snapshot": snapshot,
        "download": {
            "pdf": f"/api/analytics/weeks/{week_id}/download?format=pdf",
            "json": f"/api/analytics/weeks/{week_id}/download?format=json",
        }
        if week.status == "consolidated"
        else None,
    }


@router.get("/weeks/{week_id}/download")
def download_consolidation(
    week_id: UUID,
    format: str = Query("pdf", alias="format"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Descarga el consolidado (.pdf con tabla top 5 y gráficas, o .json)."""
    _require_company_staff(user)
    week = db.query(AnalyticsWeek).filter(AnalyticsWeek.id == week_id).first()
    if not week:
        raise HTTPException(404, "Semana no encontrada.")
    if week.client_code != user.client_code and user.role != "admin":
        raise HTTPException(403, "Sin acceso.")

    filename, media, content = analytics_service.resolve_consolidation_file(week, format)
    return Response(
        content=content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.post("/weeks/{week_id}/consolidate")
def consolidate(
    week_id: UUID,
    force: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Genera el consolidado de la semana.
    - Sin force: solo si el ciclo de 7 días ya terminó.
    - force=true: 'Forzar consolidado' anticipado; el documento indica duración real y advertencias.
    """
    _require_company_admin(user)
    return analytics_service.consolidate_week(db, user=user, week_id=week_id, force=force)
