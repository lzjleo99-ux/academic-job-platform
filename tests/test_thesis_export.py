"""T16 acceptance: 'DOCX export round-trip: exported file re-parses to a ThesisDoc satisfying the template
checks it was normalized for'; downloadable PDF report generation."""
from ajp.thesis import checks as checks_mod
from ajp.thesis.docx_export import export_normalized_docx
from ajp.thesis.parser import parse_docx
from ajp.thesis.report_pdf import render_report_pdf
from ajp.thesis.templates import load_template
from thesis_fixtures.build_fixtures import build_clean, build_defects

TEMPLATE = load_template("rs_phd_generic")


def test_pdf_report_renders_a_real_pdf(tmp_path):
    path = build_clean(tmp_path / "clean.docx")
    doc = parse_docx(path)
    report = checks_mod.run_checks(doc, TEMPLATE)
    pdf_bytes = render_report_pdf(report, document_title="Clean fixture")
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000


def test_docx_export_of_a_style_noncompliant_document_reparses_to_pass_style_compliance(tmp_path):
    """The defects fixture is built with the wrong font size (11pt) and line spacing (1.0) - style_compliance
    must fail on it, then pass once normalized and re-parsed."""
    path, _ = build_defects(tmp_path / "defects.docx")
    original = parse_docx(path)
    before = checks_mod.run_checks(original, TEMPLATE)
    before_style_fails = [f for f in before.findings if f.check == "style_compliance" and f.severity == "fail"]
    assert before_style_fails, "fixture setup assumption broken: expected style_compliance failures before export"

    exported_bytes = export_normalized_docx(original, TEMPLATE, cover_metadata={"title_sr": "Naslov", "author": "Autor"})
    exported_path = tmp_path / "normalized.docx"
    exported_path.write_bytes(exported_bytes)

    reparsed = parse_docx(exported_path)
    after = checks_mod.run_checks(reparsed, TEMPLATE)
    after_style_fails = [f for f in after.findings if f.check == "style_compliance" and f.severity == "fail"]
    assert after_style_fails == [], f"style_compliance still failing after normalization: {after_style_fails}"

    # the original upload itself was never touched
    still_original = parse_docx(path)
    assert still_original.page_style.font_size_pt == 11.0


def test_docx_export_preserves_the_original_content(tmp_path):
    path = build_clean(tmp_path / "clean.docx")
    original = parse_docx(path)
    exported = export_normalized_docx(original, TEMPLATE, cover_metadata={})
    (tmp_path / "out.docx").write_bytes(exported)
    reparsed = parse_docx(tmp_path / "out.docx")
    original_titles = {s.title for s in original.sections}
    reparsed_titles = {s.title for s in reparsed.sections}
    assert original_titles <= reparsed_titles                 # every original heading survives export
    assert len(reparsed.references) == len(original.references)
