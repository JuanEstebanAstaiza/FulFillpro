from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from backend.app.database import Base
from backend.app.models.user import GUID


class AccessLog(Base):
    """Bitácora operativa: accesos, activaciones, procesos."""

    __tablename__ = "access_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=True, index=True)
    license_code = Column(String(64), default="", index=True)
    label = Column(String(255), default="")
    event_type = Column(String(64), index=True)  # login, activate, process, admin, etc.
    detail = Column(Text, default="")
    device_id = Column(String(128), default="")
    ip = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class SecurityEvent(Base):
    """Incidentes de seguridad y operativos para el panel admin."""

    __tablename__ = "security_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    severity = Column(String(16), default="info", index=True)  # info | warning | critical
    category = Column(String(64), default="security", index=True)
    # security | operational | license | auth
    title = Column(String(255), default="")
    detail = Column(Text, default="")
    user_id = Column(GUID(), nullable=True)
    license_code = Column(String(64), default="")
    ip = Column(String(64), default="")
    meta = Column(JSON().with_variant(JSONB(), "postgresql"), default=dict)
    resolved = Column(Integer, default=0)  # 0 open, 1 resolved
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
