from __future__ import annotations

import re
from datetime import date
from typing import Any

from backend.app.config import get_settings
from backend.app.services.excel.reader import parse_date


def process_rows(rows: list[dict[str, Any]], today: date) -> tuple:
    """Fases 4–13 de la especificación. Retorna resumen, cant_max, reporte, prior, total_riesgo."""
    settings = get_settings()
    rows = sorted(rows, key=lambda r: r["producto"])

    by_guia: dict[str, list] = {}
    for r in rows:
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
            nombre = " + ".join(f"{r['producto']} ({r['cantidad']})" for r in items)
        else:
            r = items[0]
            var = r["variacion"]
            id_final = f"{r['id']}|{var}" if var and var not in ("nan", "") else r["id"]
            cant_res = max(r["cantidad"], 1)
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

    resumen_final = sorted(unified.values(), key=lambda r: r["PRODUCTO"])
    for row in resumen_final:
        for c in range(1, cant_max + 1):
            v = row.get(f"Cantidad {c}", 0)
            row[f"Cantidad {c}"] = int(v) if v and int(v) > 0 else ""

    reporte = sorted(
        [{"ID ORDEN": r["id"], "PRODUCTO": r["producto"], "CANTIDAD": r["cantidad"]} for r in rows],
        key=lambda r: (r["PRODUCTO"], r["CANTIDAD"]),
    )

    prior = []
    for r in rows:
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
