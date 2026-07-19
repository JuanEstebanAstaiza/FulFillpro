"""Generación de PDF de consolidado de analítica (tabla top 5 + gráficas de flujo)."""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.models.analytics import AnalyticsSaleEvent, AnalyticsWeek


def product_daily_flow(
    db: Session,
    week_id: UUID,
    top_products: list[dict],
    *,
    started_at: Optional[datetime],
    until: Optional[datetime],
) -> dict[str, Any]:
    """
    Flujo diario de unidades para los top N productos (por etiqueta producto+variación).
    """
    if not started_at:
        started_at = datetime.utcnow() - timedelta(days=7)
    if not until:
        until = datetime.utcnow()

    # lista de días desde inicio hasta until (inclusive)
    day0 = started_at.date()
    day1 = until.date()
    days: list[str] = []
    d = day0
    while d <= day1:
        days.append(d.isoformat())
        d += timedelta(days=1)
    if not days:
        days = [day0.isoformat()]

    series: list[dict] = []
    for p in top_products[:5]:
        name = p.get("product_name") or ""
        var = p.get("variation") or ""
        q = db.query(
            func.date(AnalyticsSaleEvent.first_seen_at).label("day"),
            func.sum(AnalyticsSaleEvent.quantity).label("units"),
        ).filter(
            AnalyticsSaleEvent.week_id == week_id,
            AnalyticsSaleEvent.product_name == name,
            AnalyticsSaleEvent.variation == var,
        )
        rows = q.group_by(func.date(AnalyticsSaleEvent.first_seen_at)).all()
        by_day = {str(day): int(units or 0) for day, units in rows}
        # acumular y diario
        daily = [by_day.get(day, 0) for day in days]
        cumulative = []
        acc = 0
        for v in daily:
            acc += v
            cumulative.append(acc)
        series.append(
            {
                "label": p.get("label") or name,
                "product_name": name,
                "variation": var,
                "daily": daily,
                "cumulative": cumulative,
                "total": int(p.get("units") or acc),
            }
        )

    return {"days": days, "series": series}


def _chart_bar_top5(top5: list[dict]) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [((t.get("label") or "")[:28]) for t in top5]
    units = [int(t.get("units") or 0) for t in top5]
    if not labels:
        labels, units = ["Sin datos"], [0]

    fig, ax = plt.subplots(figsize=(8.2, 3.6), dpi=140)
    colors = ["#15803d", "#16a34a", "#22c55e", "#4ade80", "#86efac"]
    bars = ax.barh(labels[::-1], units[::-1], color=colors[: len(labels)][::-1], height=0.55)
    ax.set_xlabel("Unidades vendidas (únicas)")
    ax.set_title("Top 5 productos del periodo")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for bar, val in zip(bars, units[::-1]):
        ax.text(
            bar.get_width() + max(units) * 0.02 if max(units) else 0.1,
            bar.get_y() + bar.get_height() / 2,
            str(val),
            va="center",
            fontsize=9,
            color="#0f172a",
        )
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _chart_flow(flow: dict[str, Any]) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    days = flow.get("days") or []
    series = flow.get("series") or []
    # etiquetas cortas dd/mm
    xlabels = []
    for d in days:
        try:
            xlabels.append(datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m"))
        except Exception:
            xlabels.append(d[-5:])

    fig, ax = plt.subplots(figsize=(8.2, 3.8), dpi=140)
    palette = ["#15803d", "#2563eb", "#ea580c", "#7c3aed", "#db2777"]
    if not series:
        ax.text(0.5, 0.5, "Sin datos de flujo", ha="center", va="center")
        ax.set_axis_off()
    else:
        for i, s in enumerate(series):
            y = s.get("cumulative") or s.get("daily") or []
            ax.plot(
                range(len(days)),
                y,
                marker="o",
                linewidth=2,
                markersize=4,
                color=palette[i % len(palette)],
                label=(s.get("label") or f"P{i+1}")[:32],
            )
        ax.set_xticks(range(len(days)))
        ax.set_xticklabels(xlabels, rotation=30, ha="right", fontsize=8)
        ax.set_ylabel("Unidades acumuladas")
        ax.set_title("Flujo acumulado de los top 5 (por día de primera aparición)")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(loc="upper left", fontsize=7, frameon=False)
        ax.grid(axis="y", linestyle="--", alpha=0.35)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _chart_daily_bars(flow: dict[str, Any]) -> bytes:
    """Barras apiladas de unidades nuevas por día (top 5)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    days = flow.get("days") or []
    series = flow.get("series") or []
    xlabels = []
    for d in days:
        try:
            xlabels.append(datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m"))
        except Exception:
            xlabels.append(str(d)[-5:])

    fig, ax = plt.subplots(figsize=(8.2, 3.6), dpi=140)
    palette = ["#15803d", "#2563eb", "#ea580c", "#7c3aed", "#db2777"]
    if not series or not days:
        ax.text(0.5, 0.5, "Sin datos diarios", ha="center", va="center")
        ax.set_axis_off()
    else:
        x = np.arange(len(days))
        bottom = np.zeros(len(days))
        for i, s in enumerate(series):
            vals = np.array(s.get("daily") or [0] * len(days), dtype=float)
            ax.bar(
                x,
                vals,
                bottom=bottom,
                color=palette[i % len(palette)],
                label=(s.get("label") or f"P{i+1}")[:28],
                width=0.7,
            )
            bottom = bottom + vals
        ax.set_xticks(x)
        ax.set_xticklabels(xlabels, rotation=30, ha="right", fontsize=8)
        ax.set_ylabel("Unidades nuevas / día")
        ax.set_title("Ingreso diario de unidades (top 5)")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(loc="upper left", fontsize=7, frameon=False)
        ax.grid(axis="y", linestyle="--", alpha=0.35)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def build_consolidation_pdf(
    *,
    week: AnalyticsWeek,
    snapshot: dict[str, Any],
    flow: dict[str, Any],
) -> bytes:
    """PDF: portada, advertencias, tabla top 5, gráficas de ranking y flujo."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm, mm
    from reportlab.platypus import (
        Image,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    top5 = (snapshot.get("top_products") or [])[:5]
    early = bool(snapshot.get("early_consolidation"))
    days_length = snapshot.get("days_length", "?")
    company = snapshot.get("company_name") or week.company_name or "Empresa"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1.6 * cm,
        rightMargin=1.6 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.4 * cm,
        title=f"Consolidado FulfillPro — {company}",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleFP",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=colors.HexColor("#052e16"),
        spaceAfter=6,
        alignment=TA_LEFT,
    )
    h2 = ParagraphStyle(
        "H2FP",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=colors.HexColor("#15803d"),
        spaceBefore=10,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "BodyFP",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155"),
    )
    warn_style = ParagraphStyle(
        "WarnFP",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#9a3412"),
        backColor=colors.HexColor("#fff7ed"),
        borderPadding=6,
    )
    small = ParagraphStyle(
        "SmallFP",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#64748b"),
    )

    story = []
    story.append(Paragraph("FulfillPro — Consolidado de productos más vendidos", title_style))
    story.append(
        Paragraph(
            f"<b>Empresa:</b> {company} &nbsp;|&nbsp; <b>Código:</b> {snapshot.get('client_code') or week.client_code}",
            body,
        )
    )
    tipo = "CONSOLIDADO TEMPRANO (forzado)" if early else "CONSOLIDADO COMPLETO"
    story.append(
        Paragraph(
            f"<b>Tipo:</b> {tipo}<br/>"
            f"<b>Longitud del periodo:</b> {days_length} día(s) de {snapshot.get('period', {}).get('planned_days', 7)} planificados<br/>"
            f"<b>Inicio:</b> {snapshot.get('started_at') or '—'}<br/>"
            f"<b>Generado:</b> {snapshot.get('consolidated_at') or snapshot.get('generated_at') or '—'}<br/>"
            f"<b>Unidades totales (únicas):</b> {snapshot.get('total_units', 0)} &nbsp;|&nbsp; "
            f"<b>Líneas:</b> {snapshot.get('unique_lines', 0)} &nbsp;|&nbsp; "
            f"<b>Archivos:</b> {snapshot.get('files_ingested', 0)}",
            body,
        )
    )
    story.append(Spacer(1, 6))

    if early or snapshot.get("warnings"):
        warn_txt = "<br/>".join(
            snapshot.get("warnings")
            or [
                "Este consolidado se generó de forma anticipada. "
                "Los datos pueden generar incoherencias con análisis posteriores."
            ]
        )
        story.append(Paragraph(f"<b>Advertencia</b><br/>{warn_txt}", warn_style))
        story.append(Spacer(1, 8))

    # Tabla top 5
    story.append(Paragraph("Tabla resumida — Top 5 productos", h2))
    table_data = [["#", "Producto", "Variación", "Unidades", "Líneas"]]
    for i, t in enumerate(top5, 1):
        table_data.append(
            [
                str(i),
                (t.get("product_name") or "")[:48],
                (t.get("variation") or "—")[:24],
                str(t.get("units") or 0),
                str(t.get("lines") or 0),
            ]
        )
    if len(table_data) == 1:
        table_data.append(["—", "Sin productos en el periodo", "—", "0", "0"])

    tbl = Table(table_data, colWidths=[1 * cm, 7.2 * cm, 3.2 * cm, 2.2 * cm, 2.2 * cm])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#15803d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (3, 0), (-1, -1), "CENTER"),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f0fdf4")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f0fdf4"), colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbf7d0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(tbl)
    story.append(Spacer(1, 10))

    # Gráficas
    try:
        bar_png = _chart_bar_top5(top5)
        story.append(Paragraph("Gráfica — Ranking de unidades (top 5)", h2))
        story.append(Image(io.BytesIO(bar_png), width=16.5 * cm, height=7.2 * cm))
        story.append(Spacer(1, 8))

        flow_png = _chart_flow(flow)
        story.append(Paragraph("Gráfica — Flujo acumulado a lo largo del periodo", h2))
        story.append(
            Paragraph(
                "Cada punto suma las unidades únicas registradas ese día para el producto "
                "(sin recontar órdenes duplicadas entre archivos).",
                small,
            )
        )
        story.append(Image(io.BytesIO(flow_png), width=16.5 * cm, height=7.4 * cm))
        story.append(Spacer(1, 8))

        daily_png = _chart_daily_bars(flow)
        story.append(Paragraph("Gráfica — Unidades nuevas por día (apiladas, top 5)", h2))
        story.append(Image(io.BytesIO(daily_png), width=16.5 * cm, height=7.2 * cm))
    except Exception as e:
        story.append(Paragraph(f"No se pudieron renderizar las gráficas: {e}", body))

    story.append(Spacer(1, 12))
    story.append(
        Paragraph(
            "Documento generado por FulfillPro · Uso exclusivo de la empresa contratante · "
            "Órdenes deduplicadas por guía/producto/variación dentro del ciclo.",
            small,
        )
    )

    doc.build(story)
    return buf.getvalue()
