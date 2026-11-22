# -*- coding: utf-8 -*-
"""PDF-отчёт к расчёту работ ГКЛ-потолков.

Содержит: расчёт по проекту, ведомость работ (с посчётом по смете),
2D-чертёж каркаса и предупреждения. Использует reportlab.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle, Image, PageBreak)

BASE_DIR = Path(__file__).resolve().parent

# Bundle the same Cyrillic font for every deployment.
for name in ("DejaVu", "DejaVu-Bold"):
    filename = name.replace("DejaVu", "DejaVuSans") + ".ttf"
    pdfmetrics.registerFont(TTFont(name, str(BASE_DIR / "fonts" / filename)))


def _styles():
    ss = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=ss["Title"], fontName="DejaVu-Bold",
                           fontSize=18, alignment=1, spaceAfter=6)
    subtitle = ParagraphStyle("subtitle", parent=ss["Normal"], fontName="DejaVu",
                              fontSize=11, alignment=1,
                              textColor=colors.HexColor("#555555"),
                              spaceAfter=14)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="DejaVu-Bold",
                        fontSize=13, spaceBefore=12, spaceAfter=6,
                        textColor=colors.HexColor("#1f4e79"))
    body = ParagraphStyle("body", parent=ss["Normal"], fontName="DejaVu",
                          fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=ss["Normal"], fontName="DejaVu",
                           fontSize=8.5, leading=11,
                           textColor=colors.HexColor("#666666"))
    cell = ParagraphStyle("cell", parent=ss["Normal"], fontName="DejaVu",
                          fontSize=8.5, leading=11)
    cellb = ParagraphStyle("cellb", parent=cell, fontName="DejaVu-Bold")
    return title, subtitle, h2, body, small, cell, cellb


def build_report(calc: dict, project: dict, out_pdf: str | Path, png: Path | None = None) -> str:
    """Строит PDF-отчёт. calc — result_to_dict(), project — project.json."""
    title, subtitle, h2, body, small, cell, cellb = _styles()
    pdf = str(out_pdf)
    doc = SimpleDocTemplate(pdf, pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=16*mm, bottomMargin=16*mm,
                            title="Расчёт работ ГКЛ-потолков")
    story = []

    # --- Заголовок ---
    story.append(Paragraph("Расчёт работ ГКЛ-потолков", title))
    story.append(Paragraph("Калькулятор: разбор дизайн-проекта + стыковка со сметой работ",
                           subtitle))

    # --- Исходные данные ---
    rooms = project.get("rooms", [])
    total_area = calc.get("ceiling_area", 0)
    story.append(Paragraph("1. Исходные данные", h2))
    data = [
        ["Помещения", "Площадь, м²"],
    ]
    for r in rooms:
        data.append([r.get("name", ""), f"{r.get('area_m2', 0)}"])
    data.append(["Итого площадь потолков", f"{total_area}"])
    t = Table(data, colWidths=[100*mm, 55*mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "DejaVu"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#aaaaaa")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbe5f1")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eef3f8")),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"теневой профиль: "
        f"{'да' if project.get('has_shadow_profile') else 'нет'}, "
        f"ниши под подсветку: "
        f"{'да' if project.get('has_niches') else 'нет'}.", body))

    # --- Ведомость работ ---
    story.append(Paragraph("2. Ведомость работ", h2))
    rows = [["Код", "Наименование работы", "Элемент", "Ед.", "Кол-во",
             "Цена, ₽", "Сумма, ₽"]]
    for it in calc.get("items", []):
        rows.append([
            str(it.get("code", "")),
            Paragraph(str(it.get("name", "")).replace("&", "&amp;"), cell),
            Paragraph(str(it.get("element", "")).replace("&", "&amp;"), cell),
            it.get("unit", ""),
            f"{it.get('quantity', 0):g}",
            f"{it.get('price', 0):.0f}",
            f"{it.get('total', 0):.0f}",
        ])
    rows.append(["", "ИТОГО", "", "", "", "", f"{calc.get('total_price', 0):.0f}"])
    t2 = Table(rows, colWidths=[14*mm, 62*mm, 45*mm, 14*mm, 16*mm, 16*mm, 20*mm],
               repeatRows=1)
    style2 = [
        ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "DejaVu"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#aaaaaa")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbe5f1")),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    style2.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eef3f8")))
    style2.append(("FONTNAME", (0, -1), (-1, -1), "DejaVu-Bold"))
    t2.setStyle(TableStyle(style2))
    story.append(t2)

    # --- Предупреждения и графическая доп. работа ---
    warnings = calc.get("warnings", [])
    graphic = calc.get("graphic_extra", [])
    if warnings or graphic:
        story.append(Paragraph("3. Проверка и замечания", h2))
        for w in warnings:
            story.append(Paragraph(f"⚠ {w}", small))
        for g in graphic:
            story.append(Paragraph(f"◆ {g}", small))
        if warnings or graphic:
            story.append(Spacer(1, 4))

    # --- Чертеж ---
    story.append(PageBreak())
    story.append(Paragraph("4. Контуры потолков", h2))
    if png is not None and png.exists():
        img = Image(str(png))
        # масштаб по ширине
        avail = A4[0] - 36*mm
        w, h = img.imageWidth, img.imageHeight
        if w > avail:
            scale = avail / w
            img.drawWidth = w * scale
            img.drawHeight = h * scale
        story.append(img)
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "Распознанные контуры по страницам PDF. Требуется сверка с планом.", small))

    doc.build(story)
    return pdf


if __name__ == "__main__":
    import json
    from engine import load_project, load_smeta, calculate, result_to_dict
    proj = load_project()
    smeta = load_smeta()
    res = calculate(proj, smeta)
    calc = result_to_dict(res)
    pdf = build_report(calc, proj, BASE_DIR / "calc_report.pdf")
    print("Отчёт:", pdf)

