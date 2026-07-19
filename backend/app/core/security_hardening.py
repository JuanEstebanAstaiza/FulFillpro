"""Controles de endurecimiento OWASP: secretos, headers, validaciones de producción."""
from __future__ import annotations

import logging
import secrets as secrets_mod

from backend.app.config import Settings

logger = logging.getLogger("fulfillpro.security")

# Secretos / valores que NUNCA deben usarse en production
_WEAK_JWT = {
    "dev-secret-change-in-production",
    "cambia-este-secreto-en-produccion-usa-uno-largo",
    "dev-fulfillpro-secret-change-me-in-prod",
    "change-me",
    "secret",
    "jwt-secret",
}

_WEAK_PASSWORDS = {
    "AdminFulfillPro2026!",
    "DemoEmpresa2026!",
    "admin",
    "password",
    "12345678",
    "changeme",
}


def assert_production_secrets(settings: Settings) -> None:
    """
    Fail-fast en producción si secretos o contraseñas bootstrap son débiles/default.
    En development solo emite warning.
    """
    issues: list[str] = []

    jwt = (settings.jwt_secret or "").strip()
    if not jwt or jwt in _WEAK_JWT or len(jwt) < 32:
        issues.append(
            "JWT_SECRET débil o por defecto (mín. 32 caracteres aleatorios, no valores de ejemplo)."
        )

    admin_pw = (settings.admin_password or "").strip()
    if admin_pw in _WEAK_PASSWORDS or len(admin_pw) < 12:
        issues.append(
            "ADMIN_PASSWORD débil o por defecto (mín. 12 caracteres y no usar contraseñas de demo)."
        )

    if settings.is_production:
        if settings.cors_origins.strip() == "*":
            issues.append(
                "CORS_ORIGINS='*' no está permitido en production. Define orígenes explícitos."
            )
        if issues:
            msg = "FulfillPro no puede arrancar en production:\n- " + "\n- ".join(issues)
            raise RuntimeError(msg)
    else:
        for issue in issues:
            logger.warning("[security] %s", issue)


def generate_secure_secret(nbytes: int = 48) -> str:
    return secrets_mod.token_urlsafe(nbytes)
