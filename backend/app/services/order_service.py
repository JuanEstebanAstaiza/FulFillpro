from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from backend.app.models.order import Order, OrderFile
from backend.app.models.user import User
from backend.app.services import storage_service
from backend.app.services.audit_service import log_access, log_security
from backend.app.services.excel import build_excel, process_rows, read_excel_rows
from backend.app.services.license_service import (
    assert_user_license,
    company_brand,
    consume_quota,
)


def create_order_from_upload(
    db: Session,
    *,
    user: User,
    file: UploadFile,
    content: bytes,
    license_code: Optional[str] = None,
    count_quota: bool = True,
    ip: str = "",
) -> Order:
    lic = assert_user_license(db, user, license_code)
    brand = company_brand(lic, user)
    client = user.client_code or brand["company_code"] or user.email.split("@")[0].upper()

    order = Order(
        user_id=user.id,
        license_id=lic.id,
        client_code=client,
        status="uploaded",
        original_filename=file.filename or "upload.xlsx",
        device_id="",
        counted_toward_quota=bool(count_quota and lic.count_toward_global),
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    folder = storage_service.ensure_order_dirs(order.client_code, order.id, order.created_at)
    order.storage_folder = storage_service.relative_to_storage(folder)

    path, rel, size = storage_service.save_bytes(
        folder, "input", order.original_filename, content
    )
    db.add(
        OrderFile(
            order_id=order.id,
            kind="input",
            filename=path.name,
            relative_path=rel,
            size_bytes=size,
        )
    )
    db.commit()
    db.refresh(order)

    log_access(
        db,
        event_type="upload",
        detail=f"Subida {order.original_filename} ({size} bytes) · {brand['company_name']}",
        user_id=user.id,
        license_code=lic.code,
        label=lic.label,
        ip=ip,
    )
    return order


def process_order(
    db: Session,
    *,
    user: User,
    order: Order,
    license_code: Optional[str] = None,
    ip: str = "",
) -> Order:
    if order.user_id != user.id and user.role != "admin":
        # Misma empresa puede re-procesar si comparten client_code
        if not (user.client_code and order.client_code == user.client_code):
            raise HTTPException(403, "No tienes acceso a esta orden.")

    lic = assert_user_license(db, user, license_code)
    brand = company_brand(lic, user)

    input_file = next((f for f in order.files if f.kind == "input"), None)
    if not input_file:
        raise HTTPException(400, "La orden no tiene archivo de entrada.")

    abs_path = storage_service.absolute_from_relative(input_file.relative_path)
    if not abs_path.exists():
        raise HTTPException(404, "Archivo de entrada no encontrado en almacenamiento.")

    order.status = "processing"
    db.commit()

    try:
        content = abs_path.read_bytes()
        rows = read_excel_rows(content)
        if not rows:
            raise ValueError("El archivo no tiene datos.")

        today = date.today()
        resumen_final, cant_max, reporte, prior, total_riesgo = process_rows(rows, today)
        cant_cols = [f"Cantidad {c}" for c in range(1, cant_max + 1)]
        output = build_excel(
            resumen_final,
            cant_cols,
            cant_max,
            reporte,
            prior,
            total_riesgo,
            today,
            company_name=brand["company_name"],
            company_code=brand["company_code"],
            license_code=brand["license_code"],
        )

        folder = storage_service.ensure_order_dirs(order.client_code, order.id, order.created_at)
        safe_co = "".join(c if c.isalnum() or c in "-_" else "_" for c in brand["company_code"])[:24]
        out_name = f"FulfillPro_{safe_co or 'OUT'}_{today.isoformat()}.xlsx"
        path, rel, size = storage_service.save_bytes(folder, "output", out_name, output)

        for f in list(order.files):
            if f.kind == "output":
                db.delete(f)

        db.add(
            OrderFile(
                order_id=order.id,
                kind="output",
                filename=path.name,
                relative_path=rel,
                size_bytes=size,
            )
        )

        total_uds = sum(int(row.get("TOTAL_UNIDADES", 0) or 0) for row in resumen_final)
        n_combos = sum(1 for r in resumen_final if str(r.get("VARIABLES", "")).upper() == "COMBO")
        meta = {
            "productos": len(resumen_final),
            "unidades": total_uds,
            "combos": n_combos,
            "lineas": len(rows),
            "prioritarias": len(prior),
            "total_riesgo": total_riesgo,
            "company_name": brand["company_name"],
            "company_code": brand["company_code"],
            "license_code": brand["license_code"],
        }
        storage_service.write_meta(folder, meta)

        order.row_count = len(rows)
        order.priority_count = len(prior)
        order.total_risk = float(total_riesgo)
        order.meta = meta
        order.status = "completed"
        order.processed_at = datetime.utcnow()
        order.error_message = ""

        counted = consume_quota(db, lic, count=order.counted_toward_quota)
        order.counted_toward_quota = counted
        db.commit()
        db.refresh(order)

        log_access(
            db,
            event_type="process",
            detail=f"{len(rows)} filas · {brand['company_name']}",
            user_id=user.id,
            license_code=lic.code,
            label=lic.label,
            ip=ip,
        )
        return order

    except HTTPException:
        raise
    except Exception as e:
        order.status = "failed"
        order.error_message = str(e)
        db.commit()
        log_security(
            db,
            title="Error procesando orden",
            detail=str(e),
            severity="critical",
            category="operational",
            user_id=user.id,
            license_code=lic.code if lic else "",
            ip=ip,
            meta={"order_id": str(order.id)},
        )
        raise HTTPException(500, f"Error procesando datos: {e}") from e


def list_orders(
    db: Session,
    *,
    user: User,
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
) -> tuple[list[Order], int]:
    q = db.query(Order).options(joinedload(Order.files))
    if user.role != "admin":
        # Histórico de la empresa (mismo client_code)
        if user.client_code:
            q = q.filter(Order.client_code == user.client_code)
        else:
            q = q.filter(Order.user_id == user.id)
    if status:
        q = q.filter(Order.status == status)
    total = q.count()
    items = (
        q.order_by(Order.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def get_order(db: Session, order_id: UUID, user: User) -> Order:
    order = (
        db.query(Order)
        .options(joinedload(Order.files))
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(404, "Orden no encontrada.")
    if user.role != "admin":
        if order.user_id != user.id and not (
            user.client_code and order.client_code == user.client_code
        ):
            raise HTTPException(403, "Sin acceso a esta orden.")
    return order
