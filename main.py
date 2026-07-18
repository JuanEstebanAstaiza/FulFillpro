"""
Punto de entrada de compatibilidad.

La aplicación v2 vive en backend.app.main.
Ejecutar:
  uvicorn backend.app.main:app --reload --port 8000
"""

from backend.app.main import app

__all__ = ["app"]
