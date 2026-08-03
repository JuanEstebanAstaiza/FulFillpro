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
    input_rows = list(rows)

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

    # Resumen: A→Z por producto (y variables)
    resumen_final = sorted(
        unified.values(),
        key=lambda r: (
            str(r.get("PRODUCTO") or "").upper(),
            str(r.get("VARIABLES") or "").upper(),
        ),
    )
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

    # Reporte Ordenado:
    # - Líneas normales: 1 fila por producto+variación (nombre con variación)
    # - Combos (misma guía, ≥2 filas): 1 sola fila con detalle completo de componentes
    #   y agrupación A→Z bajo el PRIMER producto del combo
    reporte: list[dict[str, Any]] = []
    guias_emitidas: set[str] = set()

    for r in input_rows:
        guia = str(r.get("guia") or "").strip()

        # Combo: emitir una sola vez por número de guía
        if guia and guia in combo_guias:
            if guia in guias_emitidas:
                continue
            guias_emitidas.add(guia)
            items = by_guia.get(guia) or [r]
            first = items[0]
            base_first = str(first.get("producto") or "").strip()
            # Detalle: cada componente con variación y su cantidad
            detalle = " + ".join(
                f"{product_with_variation(it)} ({int(it.get('cantidad') or 1)})" for it in items
            )
            vars_combo = " + ".join(
                _clean_var(it.get("variacion")) or "—" for it in items
            )
            ids = " / ".join(
                dict.fromkeys(str(it.get("id") or "").strip() for it in items if it.get("id"))
            )
            valor_sum = sum(float(it.get("valor") or 0) for it in items)
            ciudad = next((str(it.get("ciudad") or "").strip() for it in items if it.get("ciudad")), "")
            carrier = next(
                (str(it.get("transportadora") or "").strip() for it in items if it.get("transportadora")),
                "",
            )
            reporte.append(
                {
                    # Agrupa A→Z con el primer producto del combo
                    "PRODUCTO_BASE": base_first,
                    "VARIACION": vars_combo,
                    "PRODUCTO": f"COMBO · {detalle}" if detalle else f"COMBO · {base_first}",
                    # Un combo = 1 unidad de alistamiento/empaque
                    "CANTIDAD": 1,
                    "GUIA": guia,
                    "ID ORDEN": ids or str(first.get("id") or "").strip(),
                    "TIPO": "COMBO",
                    "CIUDAD": ciudad,
                    "TRANSPORTADORA": carrier,
                    "VALOR": valor_sum,
                }
            )
            continue

        # Orden normal (1 producto por guía) o sin guía
        base = str(r.get("producto") or "").strip()
        var = _clean_var(r.get("variacion"))
        reporte.append(
            {
                "PRODUCTO_BASE": base,
                "VARIACION": var,
                "PRODUCTO": product_with_variation(r),
                "CANTIDAD": int(r.get("cantidad") or 0),
                "GUIA": guia,
                "ID ORDEN": str(r.get("id") or "").strip(),
                "TIPO": "SIN GUIA" if not guia else "NORMAL",
                "CIUDAD": str(r.get("ciudad") or "").strip(),
                "TRANSPORTADORA": str(r.get("transportadora") or "").strip(),
                "VALOR": r.get("valor") or 0,
            }
        )

    # Orden: producto base A→Z (combos caen con el 1er producto) · variación · cant · guía
    reporte.sort(
        key=lambda r: (
            (r["PRODUCTO_BASE"] or "").upper(),
            0 if r.get("TIPO") == "COMBO" else 1,  # combos del producto primero en su bloque
            (r["VARIACION"] or "").upper(),
            int(r["CANTIDAD"] or 0),
            (r["GUIA"] or "").upper(),
            (r["ID ORDEN"] or "").upper(),
        )
    )

    prior = []
    for r in input_rows:
        fg = parse_date(r["fechaGuia"])
        if fg:
            dias = (today - fg).days
            if dias >= 1:
                prior.append(
                    {
                        "N GUIA": r["guia"],
                        "PRODUCTO": product_with_variation(r),
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
