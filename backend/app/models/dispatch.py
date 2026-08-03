"""
Consolidado diario de despachos (guías) para vendedores.

Cada procesamiento de Excel aporta líneas por guía. Tras 28 días desde la
fecha de despacho (o fecha de proceso), el día queda liberado para consulta
y descarga por la empresa.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship

from backend.app.database import Base
from backend.app.models.user import GUID


class DispatchDay(Base):
    """Día de despacho consolidado por empresa."""

    __tablename__ = "dispatch_days"
    __table_args__ = (
        UniqueConstraint("client_code", "dispatch_date", name="uq_dispatch_day_client_date"),
        Index("ix_dispatch_days_client_date", "client_code", "dispatch_date"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    client_code = Column(String(64), nullable=False, index=True)
    company_name = Column(String(255), default="")
    license_id = Column(GUID(), ForeignKey("licenses.id"), nullable=True)
    dispatch_date = Column(Date, nullable=False, index=True)

    guias_count = Column(Integer, default=0)
    orders_count = Column(Integer, default=0)
    total_value = Column(Float, default=0.0)
    files_ingested = Column(Integer, default=0)

    # Liberado a vendedores cuando hoy >= dispatch_date + 28
    released = Column(Boolean, default=False)
    released_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lines = relationship(
        "DispatchGuia",
        back_populates="day",
        cascade="all, delete-orphan",
    )


class DispatchGuia(Base):
    """Una guía / envío dentro del consolidado diario."""

    __tablename__ = "dispatch_guias"
    __table_args__ = (
        UniqueConstraint("day_id", "guia", name="uq_dispatch_guia_day"),
        Index("ix_dispatch_guias_guia", "guia"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    day_id = Column(GUID(), ForeignKey("dispatch_days.id"), nullable=False, index=True)

    guia = Column(String(128), nullable=False, default="")
    order_ref = Column(String(128), default="")
    product_summary = Column(Text, default="")
    quantity = Column(Integer, default=0)
    value = Column(Float, default=0.0)
    city = Column(String(128), default="")
    carrier = Column(String(128), default="")  # transportadora
    fecha_guia = Column(Date, nullable=True)
    # DESPACHADO | EN_TRANSITO | ENTREGADO | INCIDENCIA | DESCONOCIDO
    status = Column(String(32), default="DESPACHADO")
    status_note = Column(String(255), default="")

    source_order_id = Column(GUID(), nullable=True)
    first_seen_at = Column(DateTime, default=datetime.utcnow)

    day = relationship("DispatchDay", back_populates="lines")
