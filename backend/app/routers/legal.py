from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.dependencies import get_current_user, require_admin
from backend.app.models.legal import LegalDocument
from backend.app.models.user import User
from backend.app.services import legal_service

router = APIRouter(prefix="/api/legal", tags=["legal"])


class SignRequest(BaseModel):
    document_id: UUID
    signature_name: str = Field(min_length=3)
    accepted: bool = True


@router.get("/pending")
def pending(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return legal_service.pending_payload(db, user)


@router.post("/sign")
def sign(
    body: SignRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    user_agent: str = Header(default="", alias="User-Agent"),
):
    if not body.accepted:
        raise HTTPException(400, "Debes aceptar los términos para continuar.")
    ip = request.client.host if request.client else ""
    consent = legal_service.sign_document(
        db,
        user,
        document_id=body.document_id,
        signature_name=body.signature_name,
        ip=ip,
        user_agent=user_agent,
    )
    return {
        "ok": True,
        "signed_at": consent.signed_at.isoformat() if consent.signed_at else None,
        "signature_name": consent.signature_name,
    }


@router.get("/documents")
def list_docs(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.query(LegalDocument).order_by(LegalDocument.created_at.desc()).all()
    return [
        {
            "id": str(r.id),
            "slug": r.slug,
            "version": r.version,
            "title": r.title,
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
