"""API de consolidado diario de guías (liberación a vendedores a los 28 días)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.dependencies import require_consent
from backend.app.models.user import User
from backend.app.services import dispatch_service

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])


@router.get("/days")
def list_dispatch_days(
    released_only: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    return {
        "hold_days": dispatch_service.HOLD_DAYS,
        "items": dispatch_service.list_days(db, user, released_only=released_only),
    }


@router.get("/days/{day_id}")
def get_dispatch_day(
    day_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    return dispatch_service.day_payload(db, user, day_id)


@router.get("/days/{day_id}/download")
def download_dispatch_day(
    day_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    data, filename = dispatch_service.build_day_excel(db, user, day_id)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats")
def dispatch_stats(
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    """Top productos en dinero, ciudades y transportadoras."""
    return dispatch_service.stats_summary(db, user, days=days)
