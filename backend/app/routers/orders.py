from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.core.rate_limit import rate_limit_user_and_ip
from backend.app.database import get_db
from backend.app.dependencies import get_current_user, require_consent
from backend.app.models.user import User
from backend.app.schemas.order import OrderListResponse, OrderOut
from backend.app.services import job_queue, order_service, storage_service

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("/upload", response_model=OrderOut)
async def upload_order(
    request: Request,
    file: UploadFile = File(...),
    license_code: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    settings = get_settings()
    max_bytes = int(getattr(settings, "max_upload_mb", 25) or 25) * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(413, f"Archivo demasiado grande (máximo {max_bytes // (1024*1024)} MB).")
    ip = request.client.host if request.client else ""
    return order_service.create_order_from_upload(
        db,
        user=user,
        file=file,
        content=content,
        license_code=license_code or None,
        ip=ip,
        max_upload_bytes=max_bytes,
    )


@router.post("/{order_id}/process")
def process(
    order_id: UUID,
    request: Request,
    license_code: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    """Encola reproceso (nunca ejecuta Excel en el proceso HTTP)."""
    settings = get_settings()
    rate_limit_user_and_ip(
        request,
        scope="process",
        user_id=user.id,
        per_user=max(int(settings.rate_limit_process or 150), 10),
        per_ip=max(int(getattr(settings, "rate_limit_process_ip", 500) or 500), 50),
        window=60,
    )
    order = order_service.get_order(db, order_id, user)
    if order.status == "processing":
        raise HTTPException(409, "La orden ya se está procesando.")
    if order.status == "queued":
        return JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "status": "queued",
                "order_id": str(order.id),
                "message": "Ya estaba en cola.",
                "poll_url": f"/api/jobs/{order.id}",
            },
        )
    ip = request.client.host if request.client else ""
    order.status = "queued"
    order.error_message = ""
    db.commit()
    try:
        queued = job_queue.enqueue_process_job(
            order_id=order.id,
            user_id=user.id,
            license_code=(license_code or "").upper().strip(),
            ip=ip,
            max_queue=int(getattr(settings, "process_max_queue", 500) or 500),
        )
    except HTTPException:
        order.status = "failed"
        db.commit()
        raise
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "status": "queued",
            "order_id": str(order.id),
            "queue_position": queued.get("queue_position"),
            "queue_depth": queued.get("queue_depth"),
            "poll_url": f"/api/jobs/{order.id}",
            "download_url": f"/api/jobs/{order.id}/download",
        },
    )


@router.get("", response_model=OrderListResponse)
def list_orders(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items, total = order_service.list_orders(
        db, user=user, page=page, page_size=min(page_size, 100), status=status
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return order_service.get_order(db, order_id, user)


@router.get("/{order_id}/files/{kind}")
def download_file(
    order_id: UUID,
    kind: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    if kind not in ("input", "output", "prioritarias"):
        raise HTTPException(400, "Tipo de archivo no válido.")
    order = order_service.get_order(db, order_id, user)
    match = next((f for f in order.files if f.kind == kind), None)
    if not match:
        raise HTTPException(404, f"No hay archivo de tipo '{kind}'.")
    path = storage_service.absolute_from_relative(match.relative_path)
    if not path.exists():
        raise HTTPException(404, "Archivo no encontrado en disco.")
    return FileResponse(
        path,
        filename=match.filename,
        media_type=match.mime
        or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
