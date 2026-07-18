from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.models.audit import AccessLog, SecurityEvent


def log_access(
    db: Session,
    *,
    event_type: str,
    detail: str = "",
    user_id: Optional[UUID] = None,
    license_code: str = "",
    label: str = "",
    device_id: str = "",
    ip: str = "",
) -> None:
    db.add(
        AccessLog(
            user_id=user_id,
            license_code=license_code,
            label=label,
            event_type=event_type,
            detail=detail,
            device_id=device_id,
            ip=ip,
        )
    )
    db.commit()


def log_security(
    db: Session,
    *,
    title: str,
    detail: str = "",
    severity: str = "warning",
    category: str = "security",
    user_id: Optional[UUID] = None,
    license_code: str = "",
    ip: str = "",
    meta: Optional[dict[str, Any]] = None,
) -> None:
    db.add(
        SecurityEvent(
            severity=severity,
            category=category,
            title=title,
            detail=detail,
            user_id=user_id,
            license_code=license_code,
            ip=ip,
            meta=meta or {},
        )
    )
    db.commit()
