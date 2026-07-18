from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator, CHAR

from backend.app.database import Base


class GUID(TypeDecorator):
    """Platform-independent UUID type."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return str(value) if dialect.name != "postgresql" else value
        return str(uuid.UUID(str(value))) if dialect.name != "postgresql" else uuid.UUID(str(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class User(Base):
    __tablename__ = "users"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), default="")
    role = Column(String(32), default="client", nullable=False)  # admin | client
    client_code = Column(String(64), default="", index=True)
    company_name = Column(String(255), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    licenses = relationship("License", back_populates="owner", foreign_keys="License.owner_user_id")
    orders = relationship("Order", back_populates="user")
