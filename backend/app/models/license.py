from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from backend.app.database import Base
from backend.app.models.user import GUID


class License(Base):
    """
    Licencia flexible.

    Ejemplo trial:
      - limit_uses=50 (órdenes globales totales)
      - daily_limit=3
      - max_devices=3
      - duration 7 días vía expiry
      - uses se descuenta a nivel licencia, no por dispositivo

    Flags en features (JSON):
      - independent_upload: subidas no afectan el cupo global
      - skip_daily_limit: ignora límite diario
      - unlimited_orders: sin tope global
    """

    __tablename__ = "licenses"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    code = Column(String(64), unique=True, nullable=False, index=True)
    label = Column(String(255), default="")
    type = Column(String(32), default="standard")  # trial | standard | pro | enterprise | custom

    owner_user_id = Column(GUID(), ForeignKey("users.id"), nullable=True)
    company_name = Column(String(255), default="")

    max_devices = Column(Integer, default=3)
    limit_uses = Column(Integer, default=0)  # 0 = ilimitado (salvo features)
    uses = Column(Integer, default=0)  # contador global de órdenes procesadas
    daily_limit = Column(Integer, default=0)  # 0 = sin límite diario

    expiry = Column(Date, nullable=True)
    active = Column(Boolean, default=True)
    notes = Column(Text, default="")

    # Flexibilidad de cuotas
    count_toward_global = Column(Boolean, default=True)  # False = no descuenta del cupo global
    enforce_daily_limit = Column(Boolean, default=True)

    # Analítica de productos más vendidos
    analytics_enabled = Column(Boolean, default=True)
    # Semanas de historial a conservar (consolidados + eventos)
    analytics_weeks_retention = Column(Integer, default=12)
    # Máx. líneas únicas (eventos) por semana de analítica
    analytics_max_events_per_week = Column(Integer, default=50000)
    # Tope de almacenamiento de analítica en MB por empresa (0 = sin tope duro extra)
    analytics_storage_mb = Column(Integer, default=200)

    # features: dict libre para reglas futuras
    features = Column(JSON().with_variant(JSONB(), "postgresql"), default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_access = Column(DateTime, nullable=True)
    assigned_at = Column(DateTime, nullable=True)

    owner = relationship("User", back_populates="licenses", foreign_keys=[owner_user_id])
    devices = relationship("Device", back_populates="license", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="license")
