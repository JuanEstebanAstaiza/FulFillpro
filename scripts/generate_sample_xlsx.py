"""Genera samples/ordenes_muestra.xlsx con el layout que espera FulfillPro."""
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "ordenes_muestra.xlsx"
OUT.parent.mkdir(parents=True, exist_ok=True)

wb = Workbook()
ws = wb.active
ws.title = "Ordenes"

headers = [
    "ID",
    "NUMERO GUIA",
    "PRODUCTO",
    "VARIACION",
    "CANTIDAD",
    "TOTAL DE LA ORDEN",
    "FECHA GUIA GENERADA",
]
ws.append(headers)

today = date.today()
rows = [
    # orden simple
    ["1001", "G-10001", "Almohada Ortopedica Premium", "Estandar", 1, 89000, (today - timedelta(days=3)).strftime("%d/%m/%Y")],
    ["1002", "G-10002", "Almohada Ortopedica Premium", "Estandar", 2, 160000, (today - timedelta(days=1)).strftime("%d/%m/%Y")],
    ["1003", "G-10003", "Freidora Digital (2)", "", 1, 220000, today.strftime("%d/%m/%Y")],
    # combo: misma guía, dos líneas
    ["2001", "240049656479", "Almohada Ortopedica Premium Quality", "", 1, 45000, (today - timedelta(days=5)).strftime("%d/%m/%Y")],
    ["2001", "240049656479", "Almohada Ortopedica Premium Quality", "", 1, 45000, (today - timedelta(days=5)).strftime("%d/%m/%Y")],
    ["1004", "G-10004", "Termo Acero 1L", "Negro", 3, 135000, (today - timedelta(days=2)).strftime("%d/%m/%Y")],
    ["1005", "G-10005", "Termo Acero 1L", "Rojo", 1, 45000, (today - timedelta(days=0)).strftime("%d/%m/%Y")],
]

for r in rows:
    ws.append(r)

wb.save(OUT)
print(f"Escrito: {OUT}")
