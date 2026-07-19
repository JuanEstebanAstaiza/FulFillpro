from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException

from backend.app.config import get_settings


def _safe_segment(value: str) -> str:
    value = (value or "default").strip() or "default"
    value = re.sub(r"[^\w\-.]+", "_", value, flags=re.UNICODE)
    return value[:80]


def storage_root_resolved() -> Path:
    return Path(get_settings().storage_root).resolve()


def order_folder(client_code: str, order_id: UUID, when: Optional[datetime] = None) -> Path:
    """storage/{client}/{YYYY}/{MM}/{order_id}/"""
    when = when or datetime.utcnow()
    base = Path(get_settings().storage_root)
    path = (
        base
        / _safe_segment(client_code)
        / f"{when.year:04d}"
        / f"{when.month:02d}"
        / str(order_id)
    )
    return path


def ensure_order_dirs(client_code: str, order_id: UUID, when: Optional[datetime] = None) -> Path:
    root = order_folder(client_code, order_id, when)
    for sub in ("input", "output", "prioritarias"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def relative_to_storage(path: Path) -> str:
    base = storage_root_resolved()
    try:
        return str(path.resolve().relative_to(base)).replace("\\", "/")
    except ValueError as e:
        raise HTTPException(500, "Ruta de almacenamiento fuera del root permitido.") from e


def absolute_from_relative(rel: str) -> Path:
    """
    Resuelve una ruta relativa bajo STORAGE_ROOT.
    Bloquea path traversal (../, absolutas, symlinks fuera del root).
    """
    if not rel or not str(rel).strip():
        raise HTTPException(400, "Ruta de archivo vacía.")

    raw = str(rel).replace("\\", "/").strip()
    # rechazar absolutas y componentes peligrosos
    if raw.startswith("/") or raw.startswith("~") or ":" in raw.split("/")[0]:
        raise HTTPException(400, "Ruta de archivo no permitida.")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HTTPException(400, "Path traversal no permitido.")

    base = storage_root_resolved()
    candidate = (base.joinpath(*parts)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as e:
        raise HTTPException(400, "Ruta fuera del almacenamiento autorizado.") from e
    return candidate


def save_bytes(folder: Path, subdir: str, filename: str, data: bytes) -> tuple[Path, str, int]:
    # Límite duro de subida (50 MB) — defensa en profundidad además del reverse proxy
    max_bytes = 50 * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"Archivo demasiado grande (máx. {max_bytes // (1024*1024)} MB).")

    target_dir = folder / _safe_segment(subdir)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_segment(filename) or "file.xlsx"
    # solo extensiones de negocio
    lower = safe_name.lower()
    if not lower.endswith((".xlsx", ".xls", ".json", ".pdf", ".txt")):
        safe_name += ".xlsx"
    path = target_dir / safe_name
    path.write_bytes(data)
    # asegurar que el path escrito sigue bajo storage
    abs_path = path.resolve()
    base = storage_root_resolved()
    try:
        abs_path.relative_to(base)
    except ValueError as e:
        raise HTTPException(500, "Escritura fuera de storage.") from e
    return path, relative_to_storage(path), len(data)


def write_meta(folder: Path, meta: dict[str, Any]) -> Path:
    path = folder / "meta.json"
    path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return path


def ensure_storage_root() -> None:
    Path(get_settings().storage_root).mkdir(parents=True, exist_ok=True)
