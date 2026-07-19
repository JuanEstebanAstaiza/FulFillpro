"""
Worker de procesamiento de Excel.

Ejecutar:
  python -m backend.app.worker

Variables:
  WORKER_CONCURRENCY  — jobs en paralelo por proceso (default 4; bajar a 2–3 si OOM)
  WORKER_ID           — identificador opcional para logs

La API acepta 100+ envíos concurrentes en cola Redis; este proceso solo ejecuta
N Excel en paralelo (RAM acotada). Escalar con:
  docker compose up -d --scale worker=2
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from fastapi import HTTPException

from backend.app.config import get_settings
from backend.app.database import SessionLocal
from backend.app.models.order import Order
from backend.app.models.user import User
from backend.app.services import job_queue, order_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker %(process)d] %(levelname)s %(message)s",
)
log = logging.getLogger("fulfillpro.worker")

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info("Señal %s recibida, drenando…", signum)
    _shutdown = True


def _run_one(payload: dict) -> None:
    order_id = payload.get("order_id")
    user_id = payload.get("user_id")
    license_code = payload.get("license_code") or None
    ip = payload.get("ip") or ""
    if not order_id:
        return

    job_queue.update_job(order_id, status="processing", progress=10, stage="Iniciando", error="")
    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == UUID(str(order_id))).first()
        if not order:
            job_queue.update_job(
                order_id, status="failed", progress=100, stage="Error", error="Orden no encontrada"
            )
            job_queue.incr_meta("failed_total")
            return
        user = db.query(User).filter(User.id == UUID(str(user_id or order.user_id))).first()
        if not user:
            job_queue.update_job(
                order_id, status="failed", progress=100, stage="Error", error="Usuario no encontrado"
            )
            order.status = "failed"
            order.error_message = "Usuario no encontrado"
            db.commit()
            job_queue.incr_meta("failed_total")
            return

        order.status = "processing"
        db.commit()

        job_queue.update_job(order_id, status="processing", progress=25, stage="Procesando Excel")
        order_service.process_order(
            db,
            user=user,
            order=order,
            license_code=license_code,
            ip=ip,
        )
        db.refresh(order)
        final_status = order.status or "completed"
        job_queue.update_job(
            order_id,
            status=final_status,
            progress=100,
            stage="Completado" if final_status == "completed" else "Error",
            error=order.error_message or "",
            priority_count=str(order.priority_count or 0),
            total_risk=str(int(order.total_risk or 0)),
            row_count=str(order.row_count or 0),
        )
        if final_status == "completed":
            job_queue.incr_meta("completed_total")
        else:
            job_queue.incr_meta("failed_total")
        log.info("Job %s → %s", order_id, final_status)
    except HTTPException as he:
        detail = str(he.detail) if he.detail else str(he)
        log.error("Job %s HTTP %s: %s", order_id, he.status_code, detail)
        try:
            order = db.query(Order).filter(Order.id == UUID(str(order_id))).first()
            if order and order.status not in ("completed",):
                order.status = "failed"
                order.error_message = detail[:2000]
                db.commit()
        except Exception:
            db.rollback()
        job_queue.update_job(
            order_id, status="failed", progress=100, stage="Error", error=detail[:500]
        )
        job_queue.incr_meta("failed_total")
    except Exception as e:
        log.error("Job %s falló: %s\n%s", order_id, e, traceback.format_exc())
        try:
            order = db.query(Order).filter(Order.id == UUID(str(order_id))).first()
            if order and order.status not in ("completed",):
                order.status = "failed"
                order.error_message = str(e)[:2000]
                db.commit()
        except Exception:
            db.rollback()
        job_queue.update_job(
            order_id,
            status="failed",
            progress=100,
            stage="Error",
            error=str(e)[:500],
        )
        job_queue.incr_meta("failed_total")
    finally:
        job_queue.clear_processing(order_id)
        db.close()


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    settings = get_settings()
    concurrency = int(
        os.environ.get("WORKER_CONCURRENCY")
        or getattr(settings, "worker_concurrency", 4)
        or 4
    )
    concurrency = max(1, min(concurrency, 16))
    worker_id = os.environ.get("WORKER_ID", str(os.getpid()))

    log.info(
        "Worker %s arrancado · concurrency=%s · redis=%s",
        worker_id,
        concurrency,
        settings.redis_url.split("@")[-1] if settings.redis_url else "?",
    )

    try:
        from backend.app.database import Base, engine
        import backend.app.models  # noqa: F401 — registra modelos en Base.metadata

        Base.metadata.create_all(bind=engine)
    except Exception as e:
        log.warning("create_all: %s", e)

    # Verificar Redis al arranque
    for attempt in range(1, 31):
        try:
            from backend.app.redis_client import redis_ping

            if redis_ping():
                break
        except Exception:
            pass
        log.warning("Redis no listo (intento %s/30)…", attempt)
        time.sleep(1)
    else:
        log.error("Redis no disponible; abortando worker")
        return 1

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="excel") as pool:
        inflight = set()
        consecutive_empty = 0
        while not _shutdown:
            inflight = {f for f in inflight if not f.done()}
            # Propagar excepciones de futures terminados (ya logueadas en _run_one)
            for f in list(inflight):
                if f.done():
                    try:
                        f.result()
                    except Exception:
                        pass
                    inflight.discard(f)

            if len(inflight) >= concurrency:
                time.sleep(0.05)
                continue

            try:
                payload = job_queue.dequeue_blocking(timeout=2)
            except Exception as e:
                log.warning("dequeue error: %s", e)
                time.sleep(0.5)
                continue

            if not payload:
                consecutive_empty += 1
                if consecutive_empty % 30 == 0:
                    log.debug("Cola vacía (idle)…")
                continue

            consecutive_empty = 0
            log.info(
                "Tomado job %s (inflight=%s/%s queue≈%s)",
                payload.get("order_id"),
                len(inflight) + 1,
                concurrency,
                job_queue.queue_depth(),
            )
            fut = pool.submit(_run_one, payload)
            inflight.add(fut)

        log.info("Esperando %s jobs en vuelo…", len(inflight))
        for f in inflight:
            try:
                f.result(timeout=600)
            except Exception:
                pass

    log.info("Worker detenido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
