from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from backend.app.config import get_settings


def _safe_segment(value: str) -> str:
    value = (value or "default").strip() or "default"
    value = re.sub(r"[^\w\-.]+", "_", value, flags=re.UNICODE)
    return value[:80]


def order_folder(client_code: str, order_id: UUID, when: Optional[datetime] = None) -> Path:
    """storage/{client}/{YYYY}/{MM}/{order_id}/"""
    settings = get_settings()
    when = when or datetime.utcnow()
    base = Path(settings.storage_root)
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
    settings = get_settings()
    base = Path(settings.storage_root).resolve()
    try:
        return str(path.resolve().relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def absolute_from_relative(rel: str) -> Path:
    settings = get_settings()
    return Path(settings.storage_root) / rel


def save_bytes(folder: Path, subdir: str, filename: str, data: bytes) -> tuple[Path, str, int]:
    target_dir = folder / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_segment(filename) or "file.xlsx"
    if not safe_name.lower().endswith((".xlsx", ".xls", ".json")):
        safe_name += ".xlsx"
    path = target_dir / safe_name
    path.write_bytes(data)
    return path, relative_to_storage(path), len(data)


def write_meta(folder: Path, meta: dict[str, Any]) -> Path:
    path = folder / "meta.json"
    path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return path


def ensure_storage_root() -> None:
    Path(get_settings().storage_root).mkdir(parents=True, exist_ok=True)
