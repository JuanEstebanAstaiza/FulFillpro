from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.core.rate_limit import rate_limit_user_and_ip
from backend.app.database import get_db
from backend.app.dependencies import require_consent
from backend.app.models.order import Order
from backend.app.models.user import User
from backend.app.services import job_queue, order_service, storage_service

router = APIRouter(prefix="/api", tags=["process"])


async def _read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Lee el upload en chunks sin pasarse del techo (protege RAM bajo 100+ concurrentes)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                413,
                f"Archivo demasiado grande (máximo {max_bytes // (1024 * 1024)} MB).",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/process")
async def process_one_shot(
    request: Request,
    file: UploadFile = File(...),
    license_code: Optional[str] = Form(None),
    wait: str = Form("false"),
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    """
    Encola el procesamiento de Excel (no bloquea workers HTTP).

    - Por defecto responde **202** con order_id/job para polling.
    - wait=true: espera hasta 120s el resultado (compatibilidad limitada; no usar a escala).
    """
    settings = get_settings()
    # 100+ perfiles distintos: techo por usuario + techo por IP (NAT)
    rate_limit_user_and_ip(
        request,
        scope="process",
        user_id=user.id,
        per_user=max(int(settings.rate_limit_process or 150), 10),
        per_ip=max(int(getattr(settings, "rate_limit_process_ip", 500) or 500), 50),
        window=60,
    )

    max_bytes = int(getattr(settings, "max_upload_mb", 25) or 25) * 1024 * 1024
    content = await _read_upload_capped(file, max_bytes)
    ip = request.client.host if request.client else ""

    order = order_service.create_order_from_upload(
        db,
        user=user,
        file=file,
        content=content,
        license_code=license_code or None,
        ip=ip,
        max_upload_bytes=max_bytes,
    )
    order.status = "queued"
    db.commit()
    db.refresh(order)

    try:
        queued = job_queue.enqueue_process_job(
            order_id=order.id,
            user_id=user.id,
            license_code=(license_code or "").upper().strip(),
            ip=ip,
            max_queue=int(getattr(settings, "process_max_queue", 500) or 500),
        )
    except HTTPException as he:
        # Cola llena u otro error controlado: no dejar orden "fantasma" en queued eterna
        if he.status_code == 503:
            order.status = "failed"
            order.error_message = str(he.detail)[:2000]
            db.commit()
        raise
    except Exception as e:
        order.status = "failed"
        order.error_message = f"No se pudo encolar: {e}"[:2000]
        db.commit()
        raise HTTPException(503, "Cola no disponible temporalmente. Reintenta en unos segundos.") from e

    do_wait = str(wait).lower() in {"1", "true", "yes"}
    if do_wait:
        import time

        deadline = time.time() + 120
        while time.time() < deadline:
            db.expire_all()
            order = db.query(Order).filter(Order.id == order.id).first()
            if order and order.status in ("completed", "failed"):
                break
            time.sleep(0.5)
        if order and order.status == "completed":
            out = next((f for f in order.files if f.kind == "output"), None)
            if out:
                path = storage_service.absolute_from_relative(out.relative_path)
                meta = order.meta or {}
                return FileResponse(
                    path,
                    filename=out.filename,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={
                        "X-Order-Id": str(order.id),
                        "X-Priority-Count": str(order.priority_count or 0),
                        "X-Total-Risk": str(int(order.total_risk or 0)),
                        "X-Row-Count": str(order.row_count or 0),
                        "X-Company-Name": str(meta.get("company_name") or order.client_code or ""),
                        "Access-Control-Expose-Headers": (
                            "X-Order-Id, X-Priority-Count, X-Total-Risk, X-Row-Count, "
                            "X-Company-Name, Content-Disposition"
                        ),
                    },
                )
        if order and order.status == "failed":
            raise HTTPException(500, order.error_message or "Error procesando")

    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "status": "queued",
            "order_id": str(order.id),
            "job_id": str(order.id),
            "queue_position": queued.get("queue_position"),
            "queue_depth": queued.get("queue_depth") or job_queue.queue_depth(),
            "poll_url": f"/api/jobs/{order.id}",
            "download_url": f"/api/jobs/{order.id}/download",
            "message": "Trabajo encolado. Consulta poll_url hasta status=completed y descarga el Excel.",
        },
    )


@router.get("/jobs/{order_id}")
def job_status(
    order_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    order = order_service.get_order(db, order_id, user)
    job = job_queue.get_job(order_id) or {}
    # Preferir estado de DB si el worker ya escribió completed/failed
    status = order.status if order.status in ("completed", "failed", "processing", "queued") else (
        job.get("status") or order.status
    )
    if job.get("status") == "processing" and order.status == "queued":
        status = "processing"
    depth = job_queue.queue_depth()
    return {
        "order_id": str(order.id),
        "status": status,
        "progress": int(float(job.get("progress") or (100 if status == "completed" else 0))),
        "stage": job.get("stage") or order.status,
        "error": job.get("error") or order.error_message or "",
        "queue_depth": depth,
        "priority_count": order.priority_count or int(job.get("priority_count") or 0),
        "total_risk": int(order.total_risk or float(job.get("total_risk") or 0)),
        "row_count": order.row_count or int(job.get("row_count") or 0),
        "download_ready": status == "completed",
        "download_url": f"/api/jobs/{order.id}/download" if status == "completed" else None,
    }


@router.get("/jobs/{order_id}/download")
def job_download(
    order_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    order = order_service.get_order(db, order_id, user)
    if order.status != "completed":
        raise HTTPException(409, f"La orden aún no está lista (status={order.status}).")
    out = next((f for f in order.files if f.kind == "output"), None)
    if not out:
        raise HTTPException(404, "No hay archivo de salida.")
    path = storage_service.absolute_from_relative(out.relative_path)
    if not path.exists():
        raise HTTPException(404, "Archivo no encontrado en disco.")
    meta = order.meta or {}
    return FileResponse(
        path,
        filename=out.filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "X-Order-Id": str(order.id),
            "X-Priority-Count": str(order.priority_count or 0),
            "X-Total-Risk": str(int(order.total_risk or 0)),
            "X-Row-Count": str(order.row_count or 0),
            "X-Company-Name": str(meta.get("company_name") or order.client_code or ""),
            "Access-Control-Expose-Headers": (
                "X-Order-Id, X-Priority-Count, X-Total-Risk, X-Row-Count, "
                "X-Company-Name, Content-Disposition"
            ),
        },
    )


@router.get("/queue/stats")
def queue_stats(user: User = Depends(require_consent)):
    """Estadísticas de cola (cualquier usuario autenticado con consentimiento)."""
    stats = job_queue.queue_stats()
    return {
        **stats,
        "max_queue": int(getattr(get_settings(), "process_max_queue", 500) or 500),
    }
