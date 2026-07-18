from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app.models.legal import LegalDocument, UserConsent
from backend.app.models.user import User
from backend.app.services.audit_service import log_access

DEFAULT_LEGAL_BODY = """
# Términos de uso y consentimiento de FulfillPro

**Versión 1.0**

Al acceder y utilizar la plataforma FulfillPro en representación de la empresa contratante, usted declara y acepta lo siguiente:

## 1. Uso autorizado
- El acceso se otorga exclusivamente para el procesamiento de órdenes de la **empresa titular de la licencia**.
- Queda **prohibido** utilizar la plataforma, sus reportes o archivos generados para terceros, competidores u otras empresas no cubiertas por la licencia contratada.

## 2. Distintivo y propiedad de los archivos
- Todo archivo Excel generado por FulfillPro incluye un **distintivo de la empresa contratante** y de la licencia.
- Dichos archivos son de uso interno de la empresa y no pueden revenderse, redistribuirse ni presentarse como producto de otro prestador.

## 3. Confidencialidad
- Se compromete a no compartir credenciales de acceso.
- Los datos de órdenes, guías, valores y clientes son confidenciales de la empresa contratante.

## 4. Responsabilidad del usuario
- Es responsable del uso correcto de su cuenta de correo corporativo.
- El mal uso (uso para otras empresas, filtración de reportes, reventa del servicio) puede derivar en suspensión de la cuenta y de la licencia, sin perjuicio de acciones legales de la empresa o de FulfillPro.

## 5. Tratamiento de datos
- FulfillPro procesa los archivos que usted sube únicamente para generar los reportes operativos contratados.
- Los registros de acceso, IP y firmas de consentimiento se conservan con fines de seguridad y auditoría.

## 6. Firma digital
- Al escribir su nombre completo y confirmar, realiza una **firma electrónica** vinculada a su identidad, fecha, hora e IP, con validez de aceptación de estos términos.

Si no está de acuerdo, no debe continuar y debe contactar al administrador de su empresa.
""".strip()


def seed_legal_documents(db: Session) -> LegalDocument:
    doc = (
        db.query(LegalDocument)
        .filter(LegalDocument.slug == "employee_terms", LegalDocument.is_active.is_(True))
        .order_by(LegalDocument.created_at.desc())
        .first()
    )
    if doc:
        return doc
    doc = LegalDocument(
        slug="employee_terms",
        version="1.0",
        title="Términos de uso y consentimiento del colaborador",
        body=DEFAULT_LEGAL_BODY,
        is_active=True,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def get_active_document(db: Session, slug: str = "employee_terms") -> Optional[LegalDocument]:
    return (
        db.query(LegalDocument)
        .filter(LegalDocument.slug == slug, LegalDocument.is_active.is_(True))
        .order_by(LegalDocument.created_at.desc())
        .first()
    )


def user_has_signed(db: Session, user: User, document: LegalDocument) -> bool:
    return (
        db.query(UserConsent)
        .filter(
            UserConsent.user_id == user.id,
            UserConsent.document_id == document.id,
            UserConsent.accepted.is_(True),
        )
        .first()
        is not None
    )


def needs_consent(db: Session, user: User) -> bool:
    """Platform admin no firma términos de empleado. Resto sí si falta firma vigente."""
    if user.role == "admin":
        return False
    if not user.must_accept_terms and user.terms_accepted_at:
        # Aún así, si hay documento nuevo no firmado, pedir de nuevo
        pass
    doc = get_active_document(db)
    if not doc:
        return False
    return not user_has_signed(db, user, doc)


def pending_payload(db: Session, user: User) -> dict:
    doc = get_active_document(db)
    required = needs_consent(db, user)
    if not doc:
        return {"required": False, "document": None}
    return {
        "required": required,
        "document": {
            "id": str(doc.id),
            "slug": doc.slug,
            "version": doc.version,
            "title": doc.title,
            "body": doc.body,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
        },
    }


def sign_document(
    db: Session,
    user: User,
    *,
    document_id: UUID,
    signature_name: str,
    ip: str = "",
    user_agent: str = "",
) -> UserConsent:
    if user.role == "admin":
        raise HTTPException(400, "El administrador de plataforma no requiere este consentimiento.")

    name = (signature_name or "").strip()
    if len(name) < 3:
        raise HTTPException(400, "Debes escribir tu nombre completo como firma digital.")

    doc = db.query(LegalDocument).filter(LegalDocument.id == document_id, LegalDocument.is_active.is_(True)).first()
    if not doc:
        raise HTTPException(404, "Documento legal no encontrado o inactivo.")

    existing = (
        db.query(UserConsent)
        .filter(UserConsent.user_id == user.id, UserConsent.document_id == doc.id)
        .first()
    )
    if existing and existing.accepted:
        return existing

    if existing:
        existing.signature_name = name
        existing.accepted = True
        existing.ip = ip
        existing.user_agent = (user_agent or "")[:500]
        existing.signed_at = datetime.utcnow()
        consent = existing
    else:
        consent = UserConsent(
            user_id=user.id,
            document_id=doc.id,
            signature_name=name,
            accepted=True,
            ip=ip,
            user_agent=(user_agent or "")[:500],
        )
        db.add(consent)

    user.must_accept_terms = False
    user.terms_accepted_at = datetime.utcnow()
    db.commit()
    db.refresh(consent)

    log_access(
        db,
        event_type="legal_sign",
        detail=f"Firma digital '{name}' doc {doc.version}",
        user_id=user.id,
        ip=ip,
    )
    return consent
