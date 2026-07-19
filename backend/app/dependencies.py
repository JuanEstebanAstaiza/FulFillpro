from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.core.security import safe_decode
from backend.app.database import get_db
from backend.app.models.user import User
from backend.app.services import legal_service

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado.")
    payload = safe_decode(creds.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")
    user_id = payload.get("sub")
    try:
        uid = UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")
    user = db.query(User).filter(User.id == uid).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inactivo o inexistente.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Se requiere rol administrador.")
    return user


def require_consent(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """
    OWASP A01: el consentimiento legal debe exigirse en backend, no solo en UI.
    Platform admin no requiere términos de colaborador.
    """
    if legal_service.needs_consent(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Debes firmar los términos y el consentimiento legal antes de usar la plataforma. "
                "Completa el proceso en /api/legal/pending y /api/legal/sign."
            ),
        )
    return user



def get_optional_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if not creds or not creds.credentials:
        return None
    payload = safe_decode(creds.credentials)
    if not payload or payload.get("type") != "access":
        return None
    try:
        uid = UUID(str(payload.get("sub")))
    except Exception:
        return None
    return db.query(User).filter(User.id == uid, User.is_active.is_(True)).first()


def get_client_ip(x_forwarded_for: Optional[str] = Header(None), x_real_ip: Optional[str] = Header(None)) -> str:
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return x_real_ip or ""
