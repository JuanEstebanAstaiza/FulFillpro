from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from backend.app.database import Base
from backend.app.models.user import GUID


class Order(Base):
    """Cada subida/procesamiento de Excel = una orden de trabajo."""

    __tablename__ = "orders"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    license_id = Column(GUID(), ForeignKey("licenses.id"), nullable=True, index=True)

    client_code = Column(String(64), default="", index=True)
    status = Column(String(32), default="uploaded", index=True)
    # uploaded | processing | completed | failed

    original_filename = Column(String(512), default="")
    storage_folder = Column(String(1024), default="")

    row_count = Column(Integer, default=0)
    priority_count = Column(Integer, default=0)
    total_risk = Column(Float, default=0.0)
    meta = Column(JSON().with_variant(JSONB(), "postgresql"), default=dict)
    error_message = Column(Text, default="")

    counted_toward_quota = Column(Boolean, default=True)
    device_id = Column(String(128), default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="orders")
    license = relationship("License", back_populates="orders")
    files = relationship("OrderFile", back_populates="order", cascade="all, delete-orphan")


class OrderFile(Base):
    __tablename__ = "order_files"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    order_id = Column(GUID(), ForeignKey("orders.id"), nullable=False, index=True)

    kind = Column(String(32), nullable=False)  # input | output | prioritarias
    filename = Column(String(512), default="")
    relative_path = Column(String(1024), default="")
    size_bytes = Column(Integer, default=0)
    mime = Column(String(128), default="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="files")
