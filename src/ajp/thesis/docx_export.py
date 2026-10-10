"""T16: normalized .docx export. The ORIGINAL upload is never modified (see thesis/store.py) - this always
writes a brand-new file. It reconstructs the document verbatim from ``ThesisDoc.paragraphs`` (every
paragraph's own original text and style, in original order - nothing here re-types or invents content) and
then applies the template's page_style_rules (font, size, line spacing, justification, margins) globally, via
the new document's own 'Normal' style and section geometry - exactly what "DOCX style compliance" checks
for, so a re-parse of the exported file is expected to pass every style_compliance finding it previously
failed. A generated cover page is added from whatever metadata is available (never invented: a field with no
known value is simply omitted, not filled with a placeholder that looks like real data).
"""
from __future__ import annotations

import io
from typing import Any, Dict, Optional

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.shared import Cm, Pt

from ajp.thesis.parser import ThesisDoc
from ajp.thesis.templates import Template

_JUSTIFICATION = {
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY, "left": WD_ALIGN_PARAGRAPH.LEFT,
    "right": WD_ALIGN_PARAGRAPH.RIGHT, "center": WD_ALIGN_PARAGRAPH.CENTER,
}


class ExportError(ValueError):
    pass


def _apply_page_style(doc: DocxDocument, tmpl: Template) -> None:
    rules = tmpl.page_style_rules
    style = doc.styles["Normal"]
    if rules.get("font_name"):
        style.font.name = rules["font_name"]
    if rules.get("font_size_pt"):
        style.font.size = Pt(float(rules["font_size_pt"]))
    pf = style.paragraph_format
    spacing = rules.get("line_spacing")
    if spacing == 1.0:
        pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    elif spacing == 1.5:
        pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    elif spacing == 2.0:
        pf.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    elif spacing:
        pf.line_spacing = float(spacing)
    justification = _JUSTIFICATION.get(str(rules.get("justification", "")).lower())
    if justification is not None:
        pf.alignment = justification

    margins = rules.get("margins_cm") or {}
    sec = doc.sections[0]
    if "top" in margins:
        sec.top_margin = Cm(margins["top"])
    if "bottom" in margins:
        sec.bottom_margin = Cm(margins["bottom"])
    if "left" in margins:
        sec.left_margin = Cm(margins["left"])
    if "right" in margins:
        sec.right_margin = Cm(margins["right"])


def _add_cover_page(doc: DocxDocument, metadata: Dict[str, Any], tmpl: Template) -> None:
    def add(text: Optional[str], *, bold: bool = False, size: Optional[float] = None) -> None:
        if not text:
            return
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        run.bold = bold
        if size:
            run.font.size = Pt(size)

    add(metadata.get("institution"), size=13)
    for _ in range(3):
        doc.add_paragraph()
    add(metadata.get("title_sr") or metadata.get("title"), bold=True, size=16)
    if metadata.get("title_en"):
        add(metadata["title_en"], bold=True, size=14)
    for _ in range(4):
        doc.add_paragraph()
    add(("doktorska disertacija" if not metadata.get("subtitle") else metadata["subtitle"]), size=12)
    for _ in range(4):
        doc.add_paragraph()
    if metadata.get("author"):
        add(f"Autor: {metadata['author']}")
    if metadata.get("mentor"):
        add(f"Mentor: {metadata['mentor']}")
    if metadata.get("year"):
        add(str(metadata["year"]))
    add(f"[normalized to template: {tmpl.label}]", size=8)
    doc.add_page_break()


def export_normalized_docx(thesis_doc: ThesisDoc, tmpl: Template, *, cover_metadata: Optional[Dict[str, Any]] = None) -> bytes:
    if thesis_doc.source_format != "docx" or not thesis_doc.paragraphs:
        raise ExportError("normalized DOCX export requires a .docx source with a reconstructible paragraph stream")

    out = DocxDocument()
    # a fresh python-docx Document() may start with one empty paragraph, depending on version/template -
    # drop it if present so the export doesn't start with a stray blank line before the cover page.
    if out.paragraphs and not out.paragraphs[0].text and not out.paragraphs[0].runs:
        out.paragraphs[0]._p.getparent().remove(out.paragraphs[0]._p)

    _add_cover_page(out, {**thesis_doc.metadata, **(cover_metadata or {})}, tmpl)

    for rec in thesis_doc.paragraphs:
        style_name = rec.style_name if rec.style_name in {s.name for s in out.styles} else "Normal"
        try:
            out.add_paragraph(rec.text, style=style_name)
        except KeyError:
            out.add_paragraph(rec.text)                              # an exotic style name from the source - fall back

    _apply_page_style(out, tmpl)

    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()
