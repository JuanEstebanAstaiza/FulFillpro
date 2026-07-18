from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session, joinedload

from backend.app.database import get_db
from backend.app.dependencies import get_current_user
from backend.app.models.license import License
from backend.app.models.user import User
from backend.app.schemas.license import ActivateRequest, LicenseOut, ValidateDeviceRequest
from backend.app.services import license_service

router = APIRouter(prefix="/api/licenses", tags=["licenses"])


@router.post("/activate")
def activate(
    body: ActivateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ip = request.client.host if request.client else ""
    return license_service.activate_device(
        db,
        user=user,
        code=body.code,
        device_id=body.device_id,
        device_name=body.device_name,
        device_fingerprint=body.device_fingerprint,
        device_soft=body.device_soft,
        ip=ip,
    )


@router.post("/validate")
def validate(
    body: ValidateDeviceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    lic = license_service.assert_device_authorized(
        db,
        user=user,
        license_code=body.code,
        device_id=body.device_id,
        device_soft=body.device_soft,
    )
    return {"ok": True, "license": license_service.license_to_dict(db, lic)}


@router.get("/status")
def status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lic = license_service.get_user_license(db, user)
    if not lic:
        return {"ok": False, "license": None, "message": "No hay licencia asignada."}
    return {"ok": True, "license": license_service.license_to_dict(db, lic)}


@router.get("/mine", response_model=list[LicenseOut])
def mine(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lics = (
        db.query(License)
        .options(joinedload(License.devices))
        .filter(License.owner_user_id == user.id)
        .order_by(License.created_at.desc())
        .all()
    )
    return [license_service.license_to_dict(db, l) for l in lics]
