"""T16: downloadable PDF report from a CheckReport - reportlab (weasyprint needs system libs that are not
present on this dev machine, confirmed live: 'cannot load library libgobject-2.0-0' - reportlab has no such
external dependency, matching packet 008's own fallback instruction)."""
from __future__ import annotations

import io
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ajp.thesis.checks import CheckReport

_SEVERITY_COLOR = {"pass": colors.HexColor("#1a7f37"), "warn": colors.HexColor("#9a6700"),
                   "fail": colors.HexColor("#cf222e")}


def render_report_pdf(report: CheckReport, *, document_title: Optional[str] = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm)
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#555555"))

    story = [
        Paragraph("Thesis formatting report", styles["Title"]),
        Paragraph(document_title or "(untitled document)", styles["Heading3"]),
        Paragraph(f"Template: {report.template_name}", styles["Normal"]),
        Spacer(1, 0.4 * cm),
    ]
    summary = report.summary
    story.append(Paragraph(
        f"<b>{summary.get('pass', 0)}</b> passed &nbsp;&nbsp; "
        f"<font color='#9a6700'><b>{summary.get('warn', 0)}</b> warnings</font> &nbsp;&nbsp; "
        f"<font color='#cf222e'><b>{summary.get('fail', 0)}</b> failed</font>", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    rows = [["Check", "Severity", "Message", "Page"]]
    for f in report.findings:
        rows.append([f.check, f.severity.upper(), Paragraph(f.message, small), str(f.page or "")])
    table = Table(rows, colWidths=[3.2 * cm, 2 * cm, 10 * cm, 1.3 * cm], repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, f in enumerate(report.findings, start=1):
        style_cmds.append(("TEXTCOLOR", (1, i), (1, i), _SEVERITY_COLOR.get(f.severity, colors.black)))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    doc.build(story)
    return buf.getvalue()
