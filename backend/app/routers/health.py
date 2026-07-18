from fastapi import APIRouter
from sqlalchemy import text

from backend.app.database import SessionLocal
from backend.app.redis_client import redis_ping

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    db_ok = False
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False
    return {
        "ok": db_ok and redis_ping(),
        "database": db_ok,
        "redis": redis_ping(),
        "service": "FulfillPro API",
    }
