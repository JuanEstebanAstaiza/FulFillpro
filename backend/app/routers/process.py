from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.core.rate_limit import rate_limit_from_request
from backend.app.database import get_db
from backend.app.dependencies import get_current_user
from backend.app.models.user import User
from backend.app.services import order_service, storage_service

router = APIRouter(prefix="/api", tags=["process"])


@router.post("/process")
async def process_one_shot(
    request: Request,
    file: UploadFile = File(...),
    license_code: str = Form(...),
    device_id: str = Form(...),
    device_soft: str = Form(""),
    count_quota: str = Form("true"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Atajo: sube + procesa y devuelve el Excel de salida (como v1)."""
    settings = get_settings()
    rate_limit_from_request(request, "process", settings.rate_limit_process, 60)
    content = await file.read()
    ip = request.client.host if request.client else ""
    count = str(count_quota).lower() not in {"false", "0", "no", "off"}

    order = order_service.create_order_from_upload(
        db,
        user=user,
        file=file,
        content=content,
        license_code=license_code,
        device_id=device_id,
        device_soft=device_soft,
        count_quota=count,
        ip=ip,
    )
    order = order_service.process_order(
        db,
        user=user,
        order=order,
        license_code=license_code,
        device_id=device_id,
        device_soft=device_soft,
        ip=ip,
    )
    out = next((f for f in order.files if f.kind == "output"), None)
    if not out:
        from fastapi import HTTPException

        raise HTTPException(500, "No se generó el archivo de salida.")
    path = storage_service.absolute_from_relative(out.relative_path)
    return FileResponse(
        path,
        filename=out.filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"X-Order-Id": str(order.id)},
    )
