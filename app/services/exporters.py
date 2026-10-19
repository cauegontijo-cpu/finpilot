"""Exportação de relatórios em CSV e PDF.

CSV: separador `;`, UTF-8 com BOM (abre direto no Excel pt-BR), valores no formato
brasileiro e proteção contra CSV/Formula Injection — células iniciadas por = + - @ TAB CR
recebem o prefixo `'` (OWASP).
PDF: ReportLab (Python puro). Cabeçalho/rodapé com período, data de emissão no fuso
America/Sao_Paulo, critério contábil (regime) e numeração de páginas.
"""

from __future__ import annotations

import csv
import io
from xml.sax.saxutils import escape
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.domain.dates import APP_TZ, format_date_br
from app.domain.money import format_brl
from app.services.reports import Column, ReportResult

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralize_formula(value: str) -> str:
    """Impede que planilhas interpretem o conteúdo como fórmula."""
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def format_cell(value: Any, column: Column, *, for_csv: bool = False) -> str:
    if value is None or value == "":
        return "" if for_csv else "—"
    if column.kind == "money" and isinstance(value, int):
        return format_brl(value, symbol=not for_csv)
    if column.kind == "pct" and isinstance(value, Decimal | int | float):
        return f"{value}".replace(".", ",") + "%"
    if isinstance(value, date | datetime):
        return format_date_br(value)
    return str(value)


def to_csv(result: ReportResult, issued_at: datetime) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([neutralize_formula(f"{result.title} — {result.period_label}")])
    writer.writerow([neutralize_formula(f"Critério: {result.regime}"), f"Emitido em {issued_at.astimezone(APP_TZ):%d/%m/%Y %H:%M}"])
    writer.writerow([])
    writer.writerow([neutralize_formula(c.label) for c in result.columns])
    for row in result.rows:
        writer.writerow([neutralize_formula(format_cell(v, c, for_csv=True)) for v, c in zip(row, result.columns, strict=False)])
    if result.totals:
        writer.writerow([neutralize_formula(format_cell(v, c, for_csv=True)) for v, c in zip(result.totals, result.columns, strict=False)])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def to_pdf(result: ReportResult, issued_at: datetime, user_name: str) -> bytes:
    buffer = io.BytesIO()
    wide = len(result.columns) > 5
    pagesize = landscape(A4) if wide else A4
    doc = SimpleDocTemplate(
        buffer, pagesize=pagesize, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=24 * mm, bottomMargin=18 * mm,
        title=result.title, author="FinPilot",
    )
    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=8, leading=10)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")
    issued = issued_at.astimezone(APP_TZ).strftime("%d/%m/%Y %H:%M")

    def decorate(canvas: Any, document: Any) -> None:
        canvas.saveState()
        width, height = pagesize
        canvas.setFillColor(colors.HexColor("#0b1b3f"))
        canvas.rect(0, height - 16 * mm, width, 16 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(14 * mm, height - 10 * mm, f"FinPilot · {result.title}")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(width - 14 * mm, height - 10 * mm, f"Período: {result.period_label}")
        canvas.setFillColor(colors.HexColor("#5b6b82"))
        canvas.drawString(14 * mm, 10 * mm, f"Critério contábil: {result.regime} · Emitido em {issued} (America/Sao_Paulo) para {user_name}")
        canvas.drawRightString(width - 14 * mm, 10 * mm, f"Página {document.page}")
        canvas.restoreState()

    data = [[Paragraph(escape(c.label), head) for c in result.columns]]
    for row in result.rows:
        # Conteúdo do usuário é escapado: o Paragraph do ReportLab interpreta marcação.
        data.append([Paragraph(escape(format_cell(v, c)), cell) for v, c in zip(row, result.columns, strict=False)])
    if result.totals:
        data.append([Paragraph(f"<b>{escape(format_cell(v, c))}</b>", cell) for v, c in zip(result.totals, result.columns, strict=False)])
    if len(data) == 1:
        data.append([Paragraph("Sem dados no período.", cell)] + [""] * (len(result.columns) - 1))

    table = Table(data, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2453d6")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for idx, column in enumerate(result.columns):
        if column.kind in ("money", "pct", "int"):
            style.append(("ALIGN", (idx, 0), (idx, -1), "RIGHT"))
    table.setStyle(TableStyle(style))
    story: list[Any] = [
        Paragraph(f"<b>{escape(result.title)}</b>", styles["Heading2"]),
        Paragraph(escape(f"Período: {result.period_label} · Critério: {result.regime}"), styles["BodyText"]),
        Spacer(1, 4 * mm),
        table,
    ]
    for note in result.notes:
        story += [Spacer(1, 3 * mm), Paragraph(escape(note), cell)]
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()
