from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.core.rate_limit import rate_limit_from_request
from backend.app.database import get_db
from backend.app.dependencies import require_consent
from backend.app.models.user import User
from backend.app.services import order_service, storage_service

router = APIRouter(prefix="/api", tags=["process"])


@router.post("/process")
async def process_one_shot(
    request: Request,
    file: UploadFile = File(...),
    license_code: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_consent),
):
    """Sube + procesa usando la licencia de la empresa (requiere consentimiento firmado)."""
    settings = get_settings()
    rate_limit_from_request(request, "process", settings.rate_limit_process, 60)
    content = await file.read()
    ip = request.client.host if request.client else ""

    order = order_service.create_order_from_upload(
        db,
        user=user,
        file=file,
        content=content,
        license_code=license_code or None,
        ip=ip,
    )
    order = order_service.process_order(
        db,
        user=user,
        order=order,
        license_code=license_code or None,
        ip=ip,
    )
    out = next((f for f in order.files if f.kind == "output"), None)
    if not out:
        raise HTTPException(500, "No se generó el archivo de salida.")
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
                "X-Order-Id, X-Priority-Count, X-Total-Risk, X-Row-Count, X-Company-Name, Content-Disposition"
            ),
        },
    )
