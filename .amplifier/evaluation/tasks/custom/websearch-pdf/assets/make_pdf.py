"""Generate report.pdf: a realistic internal FY2031 capital budget document.

Reads like a genuine corporate finance report. It deliberately does NOT reveal
which project is the flagship (that is set by the Operations Committee and issued
separately, i.e. the memo), so answering the task requires both the memo and this
PDF. Regenerate with: python make_pdf.py
"""

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

styles = getSampleStyleSheet()
title = ParagraphStyle("t", parent=styles["Title"], fontSize=18, spaceAfter=2)
sub = ParagraphStyle("s", parent=styles["Normal"], fontSize=10, textColor=colors.HexColor("#555555"))
h = ParagraphStyle("h", parent=styles["Heading2"], fontSize=12, spaceBefore=14, spaceAfter=6)
body = ParagraphStyle("b", parent=styles["Normal"], fontSize=10.5, leading=15)

# FY2031 approved capital allocations. Figures are distinct; the flagship is
# NOT indicated here and is neither the first row nor the largest allocation.
rows = [
    ["Program", "Business Unit", "FY2031 Allocation"],
    ["Project Aurora-3", "Advanced Sensing", "$5,220,000"],
    ["Project Borealis-4", "Propulsion Systems", "$3,150,000"],
    ["Project Cinder-2", "Energy Storage", "$12,004,750"],
    ["Project Zephyr-9", "Avionics", "$8,417,200"],
    ["Project Marlin-7", "Materials R&D", "$1,980,000"],
    ["Project Solace-6", "Ground Systems", "$6,540,000"],
    ["Total", "", "$37,311,950"],
]


def build() -> None:
    doc = SimpleDocTemplate(
        "report.pdf",
        pagesize=LETTER,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        title="FY2031 Capital Program Budget",
        author="Halcyon Systems, Inc.",
    )
    story = []
    story.append(Paragraph("Halcyon Systems, Inc.", title))
    story.append(Paragraph("FY2031 Capital Program Budget &mdash; Approved Allocations", sub))
    story.append(Paragraph("Office of the Chief Financial Officer &nbsp;|&nbsp; Confidential — Internal Use Only", sub))
    story.append(Spacer(1, 0.25 * inch))
    story.append(
        Paragraph(
            "The following program allocations for fiscal year 2031 were approved by the "
            "Capital Committee at the December 2030 review. Allocations are firm for the "
            "fiscal year; mid-year reallocations require CFO approval and a revised board memo.",
            body,
        )
    )
    story.append(Spacer(1, 0.18 * inch))

    table = Table(rows, colWidths=[2.2 * inch, 2.1 * inch, 1.7 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#eef2f7")]),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor("#1f3a5f")),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.HexColor("#1f3a5f")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.22 * inch))
    story.append(
        Paragraph(
            "Flagship program designation for the fiscal year is set by the Operations "
            "Committee and issued under separate cover. Flagship status reflects strategic "
            "priority for the year and is determined independently of allocation size.",
            body,
        )
    )
    story.append(Spacer(1, 0.4 * inch))
    story.append(
        Paragraph(
            "Halcyon Systems, Inc. &nbsp;·&nbsp; FY2031 Capital Program Budget &nbsp;·&nbsp; "
            "Document HS-FIN-2031-014 &nbsp;·&nbsp; Page 1 of 1",
            sub,
        )
    )
    doc.build(story)
    print("wrote report.pdf")


if __name__ == "__main__":
    build()
