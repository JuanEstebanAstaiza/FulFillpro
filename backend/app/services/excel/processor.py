from __future__ import annotations

import re
from datetime import date
from typing import Any

from backend.app.config import get_settings
from backend.app.services.excel.reader import parse_date


def _clean_var(value: Any) -> str:
    v = str(value or "").strip()
    if not v or v.lower() in {"nan", "none", "null", "-"}:
        return ""
    return v


def product_with_variation(row: dict[str, Any]) -> str:
    """
    Nombre de producto para mostrar, incluyendo variables (talla, color, etc.)
    cuando existen. Ej: "Camiseta Básica [XL / Negro]"
    """
    nombre = str(row.get("producto") or "").strip()
    var = _clean_var(row.get("variacion"))
    if var:
        return f"{nombre} [{var}]"
    return nombre


def process_rows(rows: list[dict[str, Any]], today: date) -> tuple:
    """Fases 4–13 de la especificación. Retorna resumen, cant_max, reporte, prior, total_riesgo."""
    settings = get_settings()
    # Conservar orden de aparición del Excel de entrada (no reordenar alfabético)
    input_rows = list(rows)
    product_order: list[str] = []
    for r in input_rows:
        p = str(r.get("producto") or "").strip()
        if p and p not in product_order:
            product_order.append(p)
    product_rank = {p: i for i, p in enumerate(product_order)}

    by_guia: dict[str, list] = {}
    for r in input_rows:
        if r["guia"]:
            by_guia.setdefault(r["guia"], []).append(r)

    combo_guias = {g for g, items in by_guia.items() if len(items) > 1}
    resumen_dict: dict[str, dict] = {}
    nombre_dict: dict[str, str] = {}
    cant_max = 1

    for guia, items in by_guia.items():
        if guia in combo_guias:
            id_final = f"COMP-{guia}"
            cant_res = 1
            # Incluir talla/color/etc. en cada línea del combo para alistamiento en bodega
            nombre = " + ".join(
                f"{product_with_variation(r)} ({r['cantidad']})" for r in items
            )
        else:
            r = items[0]
            var = _clean_var(r.get("variacion"))
            id_final = f"{r['id']}|{var}" if var else r["id"]
            cant_res = max(r["cantidad"], 1)
            # En filas normales la variación va en columna VARIABLES;
            # el nombre se mantiene limpio (spec). Opcional: también enriquecer.
            nombre = r["producto"]

        cant_res = min(cant_res, settings.max_cant_cols)

        if id_final not in resumen_dict:
            resumen_dict[id_final] = {}
            nombre_dict[id_final] = nombre
        resumen_dict[id_final][cant_res] = resumen_dict[id_final].get(cant_res, 0) + 1
        cant_max = max(cant_max, cant_res)

    unified: dict[str, dict] = {}
    for key, cnts in resumen_dict.items():
        if "|" in key:
            variable = key.split("|", 1)[1]
        elif key.startswith("COMP-"):
            variable = "COMBO"
        elif re.fullmatch(r"\d+(\.\d+)?", key or ""):
            variable = ""
        else:
            variable = key

        prod = nombre_dict[key]
        if variable != "COMBO":
            prod = re.sub(r"\s*\(\d+\)\s*", " ", prod).strip()

        ukey = f"{variable}|{prod}"
        if ukey not in unified:
            unified[ukey] = {
                "VARIABLES": variable,
                "PRODUCTO": prod,
                **{f"Cantidad {c}": 0 for c in range(1, cant_max + 1)},
            }
        for c, n in cnts.items():
            unified[ukey][f"Cantidad {c}"] = unified[ukey].get(f"Cantidad {c}", 0) + n

    def _resumen_sort_key(row: dict) -> tuple:
        prod = str(row.get("PRODUCTO") or "")
        # Combos / nombres compuestos: rank del primer producto conocido
        rank = product_rank.get(prod, 10_000)
        for p, i in product_rank.items():
            if p in prod:
                rank = min(rank, i)
        return (rank, prod)

    resumen_final = sorted(unified.values(), key=_resumen_sort_key)
    for row in resumen_final:
        total_unidades = 0
        for c in range(1, cant_max + 1):
            v = row.get(f"Cantidad {c}", 0)
            n_ordenes = int(v) if v and int(v) > 0 else 0
            # Columna Cant. c = cuántas órdenes piden c unidades del producto/combo
            # Total a alistar = sumatoria (c × n_ordenes)
            total_unidades += c * n_ordenes
            row[f"Cantidad {c}"] = n_ordenes if n_ordenes > 0 else ""
        row["TOTAL_UNIDADES"] = total_unidades

    # Reporte Ordenado: mismo orden de filas del Excel de entrada (orden de generación)
    reporte = [
        {
            "ID ORDEN": r["id"],
            "PRODUCTO": r["producto"],
            "CANTIDAD": r["cantidad"],
            "GUIA": r.get("guia") or "",
            "CIUDAD": r.get("ciudad") or "",
            "TRANSPORTADORA": r.get("transportadora") or "",
            "VALOR": r.get("valor") or 0,
        }
        for r in input_rows
    ]

    prior = []
    for r in input_rows:
        fg = parse_date(r["fechaGuia"])
        if fg:
            dias = (today - fg).days
            if dias >= 1:
                prior.append(
                    {
                        "N GUIA": r["guia"],
                        "PRODUCTO": r["producto"],
                        "VALOR": r["valor"],
                        "FECHA GUIA": str(fg),
                        "DIAS RETRASO": dias,
                        "ESTADO": "URGENTE" if dias == 1 else "SUPER ATRASADA",
                        "RIESGO 20": round(r["valor"] * 0.2),
                    }
                )
    # Spec: ordenar por fecha guía ascendente (más antigua primero)
    prior.sort(key=lambda r: (r["FECHA GUIA"], -r["DIAS RETRASO"]))

    total_riesgo = sum(r["RIESGO 20"] for r in prior)
    return resumen_final, cant_max, reporte, prior, total_riesgo
