from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.orm import relationship

from backend.app.database import Base
from backend.app.models.user import GUID


class Device(Base):
    """
    Equipo registrado bajo una licencia.

    device_id: identificador asignable al PC/laptop (no es IMEI de celular obligatorio).
    El administrador o el usuario lo registra al activar.
    """

    __tablename__ = "devices"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    license_id = Column(GUID(), ForeignKey("licenses.id"), nullable=False, index=True)

    device_id = Column(String(128), nullable=False, index=True)  # ID de equipo asignado
    device_fingerprint = Column(String(255), default="")
    device_soft = Column(String(255), default="")
    device_name = Column(String(255), default="")

    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    license = relationship("License", back_populates="devices")
