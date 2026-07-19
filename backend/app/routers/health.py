from fastapi import APIRouter
from sqlalchemy import text

from backend.app.database import SessionLocal
from backend.app.redis_client import redis_ping
from backend.app.services import job_queue

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/api/health")
def health():
    db_ok = False
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False
    redis_ok = redis_ping()
    q = {}
    try:
        q = job_queue.queue_stats()
    except Exception:
        q = {}
    return {
        "ok": db_ok and redis_ok,
        "database": db_ok,
        "redis": redis_ok,
        "service": "FulfillPro API",
        "queue": q,
    }
