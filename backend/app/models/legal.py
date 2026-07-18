from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from backend.app.database import Base
from backend.app.models.user import GUID


class LegalDocument(Base):
    """Documento legal vigente (términos, privacidad, uso de la plataforma)."""

    __tablename__ = "legal_documents"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    slug = Column(String(64), default="terms", index=True)
    version = Column(String(32), nullable=False, default="1.0")
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    consents = relationship("UserConsent", back_populates="document")


class UserConsent(Base):
    """Firma digital del trabajador en el primer acceso (o nueva versión del documento)."""

    __tablename__ = "user_consents"
    __table_args__ = (UniqueConstraint("user_id", "document_id", name="uq_user_document_consent"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    document_id = Column(GUID(), ForeignKey("legal_documents.id"), nullable=False, index=True)
    signature_name = Column(String(255), nullable=False)
    accepted = Column(Boolean, default=True)
    ip = Column(String(64), default="")
    user_agent = Column(String(512), default="")
    signed_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("LegalDocument", back_populates="consents")
