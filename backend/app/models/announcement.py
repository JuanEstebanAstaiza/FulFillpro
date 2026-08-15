"""Avisos de plataforma (ops → toast a todos los usuarios)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, Text

from backend.app.database import Base
from backend.app.models.user import GUID


class PlatformAnnouncement(Base):
    __tablename__ = "platform_announcements"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    title = Column(String(200), nullable=False, default="Aviso de plataforma")
    message = Column(Text, nullable=False, default="")
    # info | warn | danger | ok
    severity = Column(String(16), default="info")
    active = Column(Boolean, default=True, index=True)
    created_by_email = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    # Si se define, deja de mostrarse automáticamente
    expires_at = Column(DateTime, nullable=True)
    deactivated_at = Column(DateTime, nullable=True)
