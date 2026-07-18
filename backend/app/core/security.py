from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from backend.app.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, extra: Optional[dict[str, Any]] = None) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "access"}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str) -> tuple[str, str]:
    """Returns (token, jti)."""
    settings = get_settings()
    jti = str(uuid4())
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expire_days)
    payload = {"sub": subject, "exp": expire, "type": "refresh", "jti": jti}
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def safe_decode(token: str) -> Optional[dict[str, Any]]:
    try:
        return decode_token(token)
    except JWTError:
        return None


def normalize_device_id(value: str) -> str:
    """Normaliza el identificador de equipo (no es IMEI de celular obligatorio)."""
    return (value or "").strip().upper().replace(" ", "")
