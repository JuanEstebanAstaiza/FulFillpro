from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from backend.app.database import Base
from backend.app.models.user import GUID


class AnalyticsWeek(Base):
    """
    Ciclo de 7 días de analítica por empresa.
    Arranca con la primera subida de Excel y termina 7 días después.
    """

    __tablename__ = "analytics_weeks"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    client_code = Column(String(64), nullable=False, index=True)
    company_name = Column(String(255), default="")
    license_id = Column(GUID(), ForeignKey("licenses.id"), nullable=True)

    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ends_at = Column(DateTime, nullable=False)
    # open | ended | consolidated
    status = Column(String(32), default="open", index=True)

    unique_orders = Column(Integer, default=0)
    unique_lines = Column(Integer, default=0)
    total_units = Column(Integer, default=0)
    files_ingested = Column(Integer, default=0)
    events_count = Column(Integer, default=0)  # filas únicas almacenadas

    consolidated_at = Column(DateTime, nullable=True)
    consolidated_by = Column(GUID(), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    events = relationship("AnalyticsSaleEvent", back_populates="week", cascade="all, delete-orphan")
    consolidation = relationship(
        "AnalyticsConsolidation",
        back_populates="week",
        uselist=False,
        cascade="all, delete-orphan",
    )


class AnalyticsSaleEvent(Base):
    """
    Línea de producto deduplicada por semana.
    dedup_key evita que la misma orden en otro Excel sume dos veces.
    """

    __tablename__ = "analytics_sale_events"
    __table_args__ = (
        UniqueConstraint("week_id", "dedup_key", name="uq_week_dedup_key"),
        Index("ix_analytics_events_week_product", "week_id", "product_name"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    week_id = Column(GUID(), ForeignKey("analytics_weeks.id"), nullable=False, index=True)

    dedup_key = Column(String(512), nullable=False)
    order_ref = Column(String(128), default="")  # id orden del excel
    guia = Column(String(128), default="")
    product_name = Column(String(512), nullable=False, default="")
    variation = Column(String(255), default="")
    quantity = Column(Integer, default=1)

    # Sin FK rígida: el order puede purgarse sin romper analítica
    source_order_id = Column(GUID(), nullable=True)
    first_seen_at = Column(DateTime, default=datetime.utcnow)

    week = relationship("AnalyticsWeek", back_populates="events")


class AnalyticsConsolidation(Base):
    """Snapshot del consolidado semanal (gráficas y ranking congelados)."""

    __tablename__ = "analytics_consolidations"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    week_id = Column(GUID(), ForeignKey("analytics_weeks.id"), unique=True, nullable=False)

    generated_at = Column(DateTime, default=datetime.utcnow)
    generated_by = Column(GUID(), nullable=True)
    snapshot = Column(JSON().with_variant(JSONB(), "postgresql"), default=dict)
    # path relativo al storage del consolidado opcional
    relative_path = Column(String(1024), default="")
    size_bytes = Column(Integer, default=0)

    week = relationship("AnalyticsWeek", back_populates="consolidation")
