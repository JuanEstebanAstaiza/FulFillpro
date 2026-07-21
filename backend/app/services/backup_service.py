"""
Backup / restore de plataforma FulfillPro (solo platform admin).

Formato del archivo (.zip):
  manifest.json   — versión, fecha, conteos, flags
  database.json   — tablas serializadas (orden de restore)
  storage/**      — archivos bajo STORAGE_ROOT (opcional)

Restauración:
  - Exige frase de confirmación
  - Crea un snapshot pre-restore en /tmp o storage/.backups
  - Reemplaza filas de BD en orden FK-safe
  - Opcionalmente reemplaza storage
"""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.models.analytics import (
    AnalyticsConsolidation,
    AnalyticsSaleEvent,
    AnalyticsWeek,
)
from backend.app.models.audit import AccessLog, SecurityEvent
from backend.app.models.device import Device
from backend.app.models.legal import LegalDocument, UserConsent
from backend.app.models.license import License
from backend.app.models.order import Order, OrderFile
from backend.app.models.user import User
from backend.app.services import storage_service

BACKUP_FORMAT_VERSION = 1
APP_NAME = "FulfillPro"

# Orden de export/import respetando FKs
TABLE_MODELS: list[tuple[str, Any]] = [
    ("users", User),
    ("legal_documents", LegalDocument),
    ("licenses", License),
    ("devices", Device),
    ("orders", Order),
    ("order_files", OrderFile),
    ("user_consents", UserConsent),
    ("analytics_weeks", AnalyticsWeek),
    ("analytics_sale_events", AnalyticsSaleEvent),
    ("analytics_consolidations", AnalyticsConsolidation),
    ("access_logs", AccessLog),
    ("security_events", SecurityEvent),
]

# Borrado en orden inverso a FKs
CLEAR_ORDER = [
    "security_events",
    "access_logs",
    "analytics_consolidations",
    "analytics_sale_events",
    "analytics_weeks",
    "user_consents",
    "order_files",
    "orders",
    "devices",
    "licenses",
    "legal_documents",
    "users",
]

SERIAL_TABLES = ("access_logs", "security_events", "user_consents")


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return None
    raise TypeError(f"No serializable: {type(obj)}")


def _row_to_dict(model: Any, row: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for col in model.__table__.columns:
        val = getattr(row, col.name)
        if isinstance(val, UUID):
            val = str(val)
        elif isinstance(val, datetime):
            val = val.isoformat() if val else None
        elif isinstance(val, date):
            val = val.isoformat() if val else None
        data[col.name] = val
    return data


def _parse_value(col, value: Any) -> Any:
    if value is None:
        return None
    type_name = type(col.type).__name__
    try:
        # UUID / GUID
        if type_name in ("GUID", "UUID") or (
            isinstance(value, str)
            and len(value) == 36
            and value.count("-") == 4
            and (col.name == "id" or col.name.endswith("_id"))
        ):
            if isinstance(value, str):
                try:
                    return UUID(value)
                except Exception:
                    return value
        if type_name in ("DateTime", "TIMESTAMP") or "DateTime" in type_name:
            if isinstance(value, str):
                s = value.replace("Z", "")
                if "+" in s[10:]:
                    s = s.rsplit("+", 1)[0]
                if s.endswith("UTC"):
                    s = s[:-3]
                return datetime.fromisoformat(s)
        if type_name == "Date" or (isinstance(value, str) and len(value) >= 10 and value[4] == "-"):
            if isinstance(value, str) and "DateTime" not in type_name and type_name != "DateTime":
                # solo si la columna es Date (expiry)
                if type_name == "Date":
                    return date.fromisoformat(value[:10])
    except Exception:
        return value
    return value


def estimate_backup(db: Session, *, include_storage: bool = True) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for name, model in TABLE_MODELS:
        counts[name] = int(db.query(model).count())

    storage_bytes = 0
    storage_files = 0
    root = storage_service.storage_root_resolved()
    if include_storage and root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                # omitir backups previos anidados
                try:
                    rel = p.relative_to(root).as_posix()
                except ValueError:
                    continue
                if rel.startswith(".backups/"):
                    continue
                storage_files += 1
                try:
                    storage_bytes += p.stat().st_size
                except OSError:
                    pass

    return {
        "format_version": BACKUP_FORMAT_VERSION,
        "table_counts": counts,
        "total_rows": sum(counts.values()),
        "storage_files": storage_files,
        "storage_bytes": storage_bytes,
        "storage_mb": round(storage_bytes / (1024 * 1024), 2),
        "include_storage_default": include_storage,
        "warning": (
            "El backup puede ser grande si hay muchos Excel en storage."
            if storage_bytes > 500 * 1024 * 1024
            else None
        ),
    }


def create_backup_zip(
    db: Session,
    *,
    include_storage: bool = True,
    created_by: str = "",
) -> tuple[Path, dict[str, Any]]:
    """
    Genera un ZIP en un archivo temporal. El caller debe servir/borrar el path.
    """
    tables: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    for name, model in TABLE_MODELS:
        rows = db.query(model).all()
        tables[name] = [_row_to_dict(model, r) for r in rows]
        counts[name] = len(tables[name])

    manifest = {
        "app": APP_NAME,
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "created_by": created_by,
        "include_storage": include_storage,
        "table_counts": counts,
        "total_rows": sum(counts.values()),
    }

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", prefix="fulfillpro-backup-")
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default),
            )
            zf.writestr(
                "database.json",
                json.dumps(
                    {"format_version": BACKUP_FORMAT_VERSION, "tables": tables},
                    ensure_ascii=False,
                    default=_json_default,
                ),
            )
            if include_storage:
                root = storage_service.storage_root_resolved()
                if root.exists():
                    for p in root.rglob("*"):
                        if not p.is_file():
                            continue
                        try:
                            rel = p.relative_to(root).as_posix()
                        except ValueError:
                            continue
                        if rel.startswith(".backups/"):
                            continue
                        # zip entry path
                        zf.write(p, arcname=f"storage/{rel}")

        size = tmp_path.stat().st_size
        manifest["archive_bytes"] = size
        manifest["archive_mb"] = round(size / (1024 * 1024), 2)
        return tmp_path, manifest
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _validate_manifest(manifest: dict) -> None:
    if not manifest:
        raise HTTPException(400, "Backup sin manifest.json.")
    if manifest.get("app") != APP_NAME:
        raise HTTPException(400, "El archivo no es un backup de FulfillPro.")
    ver = int(manifest.get("format_version") or 0)
    if ver < 1 or ver > BACKUP_FORMAT_VERSION:
        raise HTTPException(400, f"Versión de backup no soportada: {ver}.")


def peek_backup_zip(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names or "database.json" not in names:
            raise HTTPException(400, "ZIP inválido: faltan manifest.json o database.json.")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        _validate_manifest(manifest)
        storage_entries = [n for n in names if n.startswith("storage/") and not n.endswith("/")]
        return {
            **manifest,
            "has_storage": bool(storage_entries),
            "storage_entries": len(storage_entries),
            "zip_entries": len(names),
        }


def _clear_all_tables(db: Session) -> None:
    bind = db.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        # Un solo TRUNCATE CASCADE reinicia seriales y respeta FKs
        tables = ", ".join(name for name, _ in TABLE_MODELS)
        db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        db.commit()
        return
    # Fallback genérico
    for table in CLEAR_ORDER:
        db.execute(text(f"DELETE FROM {table}"))
    db.commit()


def _reset_sequences(db: Session) -> None:
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    for table in SERIAL_TABLES:
        try:
            db.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
                    f"(SELECT MAX(id) FROM {table}) IS NOT NULL)"
                )
            )
        except Exception:
            pass
    db.commit()


def _insert_rows(db: Session, name: str, model: Any, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = {c.name: c for c in model.__table__.columns}
    objs = []
    for raw in rows:
        kwargs = {}
        for k, v in raw.items():
            if k not in cols:
                continue
            kwargs[k] = _parse_value(cols[k], v)
        objs.append(model(**kwargs))
    # bulk in chunks
    chunk = 500
    for i in range(0, len(objs), chunk):
        db.add_all(objs[i : i + chunk])
        db.flush()
    db.commit()
    return len(objs)


def _restore_storage_from_zip(zf: zipfile.ZipFile, *, replace: bool) -> int:
    root = storage_service.storage_root_resolved()
    root.mkdir(parents=True, exist_ok=True)

    if replace:
        # Vaciar contenido excepto .backups
        for child in list(root.iterdir()):
            if child.name == ".backups":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass

    count = 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if not name.startswith("storage/") or name.endswith("/"):
            continue
        rel = name[len("storage/") :]
        if not rel or ".." in rel.split("/"):
            continue
        dest = (root / rel).resolve()
        try:
            dest.relative_to(root)
        except ValueError:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        count += 1
    return count


def _snapshot_before_restore(db: Session) -> Optional[Path]:
    """Guarda un backup rápido (BD + sin storage grande opcional) en storage/.backups."""
    try:
        path, _meta = create_backup_zip(db, include_storage=False, created_by="pre-restore")
        root = storage_service.storage_root_resolved()
        dest_dir = root / ".backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        dest = dest_dir / f"pre-restore-{stamp}.zip"
        shutil.move(str(path), str(dest))
        return dest
    except Exception:
        return None


def restore_backup_zip(
    db: Session,
    zip_path: Path,
    *,
    confirm_phrase: str,
    include_storage: bool = True,
    created_by: str = "",
) -> dict[str, Any]:
    phrase = (confirm_phrase or "").strip().upper()
    if phrase != "RESTAURAR":
        raise HTTPException(
            400,
            'Debes confirmar escribiendo exactamente: RESTAURAR',
        )

    if not zip_path.exists():
        raise HTTPException(400, "Archivo de backup no encontrado.")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names or "database.json" not in names:
                raise HTTPException(400, "ZIP inválido: faltan manifest.json o database.json.")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            _validate_manifest(manifest)
            payload = json.loads(zf.read("database.json").decode("utf-8"))
            tables = payload.get("tables") or {}
            if not isinstance(tables, dict):
                raise HTTPException(400, "database.json corrupto.")

            pre = _snapshot_before_restore(db)

            _clear_all_tables(db)

            restored: dict[str, int] = {}
            for name, model in TABLE_MODELS:
                rows = tables.get(name) or []
                if not isinstance(rows, list):
                    rows = []
                try:
                    restored[name] = _insert_rows(db, name, model, rows)
                except Exception as e:
                    db.rollback()
                    raise HTTPException(
                        500,
                        f"Error restaurando tabla {name}: {e}. "
                        f"Se intentó snapshot pre-restore en {pre}.",
                    ) from e

            _reset_sequences(db)

            storage_count = 0
            if include_storage and any(n.startswith("storage/") for n in names):
                storage_count = _restore_storage_from_zip(zf, replace=True)

            return {
                "ok": True,
                "restored_tables": restored,
                "total_rows": sum(restored.values()),
                "storage_files_restored": storage_count,
                "pre_restore_snapshot": str(pre) if pre else None,
                "source_backup_at": manifest.get("created_at"),
                "restored_by": created_by,
                "restored_at": datetime.utcnow().isoformat() + "Z",
                "message": (
                    "Restauración completada. Cierra sesión y vuelve a entrar. "
                    "Se guardó un snapshot pre-restore (solo BD) en storage/.backups si fue posible."
                ),
            }
    except HTTPException:
        raise
    except zipfile.BadZipFile as e:
        raise HTTPException(400, "El archivo no es un ZIP válido.") from e
    except Exception as e:
        raise HTTPException(500, f"Fallo al restaurar: {e}") from e


async def save_upload_to_temp(upload: UploadFile, max_bytes: int = 2 * 1024 * 1024 * 1024) -> Path:
    """Guarda upload en temp con techo de tamaño (default 2 GB)."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", prefix="fulfillpro-restore-")
    tmp_path = Path(tmp.name)
    total = 0
    try:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                tmp.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(413, f"Backup demasiado grande (máx {max_bytes // (1024*1024)} MB).")
            tmp.write(chunk)
        tmp.close()
        return tmp_path
    except HTTPException:
        raise
    except Exception as e:
        try:
            tmp.close()
        except Exception:
            pass
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(500, f"No se pudo leer el archivo: {e}") from e
