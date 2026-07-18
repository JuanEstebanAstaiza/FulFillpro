from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from backend.app.database import get_db
from backend.app.dependencies import get_current_user
from backend.app.models.license import License
from backend.app.models.user import User
from backend.app.schemas.license import LicenseOut
from backend.app.services import license_service

router = APIRouter(prefix="/api/licenses", tags=["licenses"])


@router.get("/status")
def status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lic = license_service.get_user_license(db, user)
    if not lic:
        return {
            "ok": False,
            "license": None,
            "message": "No hay licencia asignada a tu empresa. Contacta al administrador.",
        }
    summary = license_service.usage_summary(db, lic)
    brand = license_service.company_brand(lic, user)
    return {"ok": True, "license": summary, "brand": brand}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Estadísticas de uso para la pantalla de inicio."""
    from backend.app.models.order import Order

    lic = license_service.get_user_license(db, user)
    if not lic:
        return {
            "ok": False,
            "message": "Sin licencia",
            "license": None,
            "orders_total": 0,
            "orders_completed": 0,
            "orders_failed": 0,
            "orders_week": 0,
        }

    summary = license_service.usage_summary(db, lic)
    brand = license_service.company_brand(lic, user)

    q = db.query(Order)
    if user.role != "admin":
        if user.client_code:
            q = q.filter(Order.client_code == user.client_code)
        else:
            q = q.filter(Order.user_id == user.id)

    total = q.count()
    completed = q.filter(Order.status == "completed").count()
    failed = q.filter(Order.status == "failed").count()

    from datetime import datetime, timedelta

    week_ago = datetime.utcnow() - timedelta(days=7)
    orders_week = q.filter(Order.created_at >= week_ago).count()

    return {
        "ok": True,
        "license": summary,
        "brand": brand,
        "orders_total": total,
        "orders_completed": completed,
        "orders_failed": failed,
        "orders_week": orders_week,
        "user": {
            "email": user.email,
            "full_name": user.full_name,
            "company_name": user.company_name,
            "client_code": user.client_code,
            "role": user.role,
        },
    }


@router.get("/mine", response_model=list[LicenseOut])
def mine(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lic = license_service.get_user_license(db, user)
    if not lic:
        return []
    # Recargar con devices por si el dict lo necesita
    lic = (
        db.query(License)
        .options(joinedload(License.devices))
        .filter(License.id == lic.id)
        .first()
    )
    return [license_service.license_to_dict(db, lic)]
