"""Generate PDF sales reports using reportlab."""
import datetime
from typing import Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

from app.database.models import get_sales, get_monthly_platform_totals

PLATFORMS = ["ebay", "mercari", "poshmark"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def generate_pdf(path: str, year: int, month: Optional[int] = None):
    doc = SimpleDocTemplate(path, pagesize=letter,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    styles = getSampleStyleSheet()
    bold = ParagraphStyle("bold", parent=styles["Normal"], fontName="Helvetica-Bold")
    story = []

    # Title
    period = MONTHS[month - 1] if month else str(year)
    story.append(Paragraph(f"Baum Reseller — Sales Report: {period} {year}", styles["Title"]))
    story.append(Paragraph(f"Generated {datetime.date.today()}", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))
    story.append(HRFlowable(width="100%"))
    story.append(Spacer(1, 0.15 * inch))

    sales = get_sales(year=year, month=month)
    revenue = sum(s.get("sale_price") or 0 for s in sales)
    profit = sum(s.get("profit") or 0 for s in sales)
    margin = (profit / revenue * 100) if revenue else 0

    # Summary table
    summary = [
        ["Items Sold", "Total Revenue", "Total Profit", "Avg Margin"],
        [str(len(sales)), f"${revenue:.2f}", f"${profit:.2f}", f"{margin:.1f}%"],
    ]
    summary_tbl = Table(summary, hAlign="LEFT")
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2a2a6e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("PADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 0.25 * inch))

    # Platform breakdown
    story.append(Paragraph("Revenue by Platform", bold))
    story.append(Spacer(1, 0.1 * inch))
    by_platform: dict[str, dict] = {p: {"revenue": 0, "profit": 0, "count": 0} for p in PLATFORMS}
    for s in sales:
        p = s.get("platform", "")
        if p in by_platform:
            by_platform[p]["revenue"] += s.get("sale_price") or 0
            by_platform[p]["profit"] += s.get("profit") or 0
            by_platform[p]["count"] += 1

    plat_data = [["Platform", "Sales", "Revenue", "Profit"]]
    for p, d in by_platform.items():
        if d["count"]:
            plat_data.append([p.capitalize(), str(d["count"]),
                               f"${d['revenue']:.2f}", f"${d['profit']:.2f}"])
    if len(plat_data) > 1:
        ptbl = Table(plat_data, hAlign="LEFT")
        ptbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#444480")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f8")]),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(ptbl)
        story.append(Spacer(1, 0.25 * inch))

    # Sales detail
    story.append(Paragraph("Sales Detail", bold))
    story.append(Spacer(1, 0.1 * inch))
    if sales:
        rows = [["Date", "Item", "Platform", "Sale Price", "Fees", "Profit"]]
        for s in sales:
            rows.append([
                s.get("sale_date", "")[:10],
                (s.get("title") or "")[:40],
                s.get("platform", "").capitalize(),
                f"${(s.get('sale_price') or 0):.2f}",
                f"${(s.get('platform_fees') or 0):.2f}",
                f"${(s.get('profit') or 0):.2f}",
            ])
        dtbl = Table(rows, colWidths=[0.85*inch, 2.8*inch, 0.9*inch, 0.85*inch, 0.6*inch, 0.75*inch])
        dtbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#444480")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f8")]),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(dtbl)
    else:
        story.append(Paragraph("No sales recorded for this period.", styles["Normal"]))

    doc.build(story)
