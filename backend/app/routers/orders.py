from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.dependencies import get_current_user
from backend.app.models.user import User
from backend.app.schemas.order import OrderListResponse, OrderOut
from backend.app.services import order_service, storage_service

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("/upload", response_model=OrderOut)
async def upload_order(
    request: Request,
    file: UploadFile = File(...),
    license_code: Optional[str] = Form(None),
    count_quota: str = Form("true"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = await file.read()
    ip = request.client.host if request.client else ""
    count = str(count_quota).lower() not in {"false", "0", "no", "off"}
    return order_service.create_order_from_upload(
        db,
        user=user,
        file=file,
        content=content,
        license_code=license_code or None,
        count_quota=count,
        ip=ip,
    )


@router.post("/{order_id}/process", response_model=OrderOut)
def process(
    order_id: UUID,
    request: Request,
    license_code: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = order_service.get_order(db, order_id, user)
    ip = request.client.host if request.client else ""
    return order_service.process_order(
        db,
        user=user,
        order=order,
        license_code=license_code or None,
        ip=ip,
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
    user: User = Depends(get_current_user),
):
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
