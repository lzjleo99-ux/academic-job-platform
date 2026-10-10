"""T16 acceptance: the 3 synthetic fixtures (clean / 8+ defects / Cyrillic mixed-script); >=90% of seeded
defects detected; zero false positives on the clean fixture; parser unit tests for DOCX; template validation.
"""
import pytest

from ajp.thesis import checks as checks_mod
from ajp.thesis.parser import detect_script, parse_docx
from ajp.thesis.templates import list_templates, load_template, validate_template_schema
from thesis_fixtures.build_fixtures import build_clean, build_cyrillic, build_defects

TEMPLATE = load_template("rs_phd_generic")


# ---------------------------------------------------------------- template ---------------------------------
def test_seed_template_loads_and_is_explicitly_marked_generic():
    assert TEMPLATE.generic is True
    assert "not" in TEMPLATE.label.lower() or "illustrative" in TEMPLATE.label.lower()
    assert "rs_phd_generic" in list_templates()


def test_template_schema_validation_catches_real_problems():
    assert validate_template_schema(TEMPLATE.raw) == []
    broken = dict(TEMPLATE.raw)
    del broken["abstract_languages"]
    problems = validate_template_schema(broken)
    assert any("abstract_languages" in p for p in problems)


# ---------------------------------------------------------------- parser -------------------------------------
def test_detect_script():
    assert detect_script("Ovo je tekst na srpskom latinicom.") == "latin"
    assert detect_script("Ово је текст на српском ћирилицом.") == "cyrillic"
    assert detect_script("123 456 !!!") is None


def test_parse_docx_clean_fixture_extracts_the_expected_shape(tmp_path):
    path = build_clean(tmp_path / "clean.docx")
    doc = parse_docx(path)
    assert doc.source_format == "docx"
    assert len(doc.abstracts) == 2
    langs = {a.lang for a in doc.abstracts}
    assert langs == {"sr", "en"}
    titles = [s.title for s in doc.sections]
    assert "Uvod" in titles and "Rezultati" in titles and "Literatura" in titles
    assert len(doc.references) == 10
    assert doc.page_style.font_name == "Times New Roman"
    assert doc.page_style.font_size_pt == 12.0
    assert doc.page_style.margins_cm["top"] == 2.5


# ---------------------------------------------------------------- clean: zero false positives -----------------
def test_clean_fixture_has_zero_warn_or_fail_findings(tmp_path):
    path = build_clean(tmp_path / "clean.docx")
    doc = parse_docx(path)
    report = checks_mod.run_checks(doc, TEMPLATE)
    # the one accepted, inherent limitation: page_numbering can never be a hard pass/fail on a .docx source
    # (no fixed pagination before printing/export - see check_page_numbering's own docstring), always 'warn'.
    bad = [f for f in report.findings if f.severity != "pass" and f.check != "page_numbering"]
    assert bad == [], f"unexpected non-pass findings on the CLEAN fixture: {bad}"
    assert report.summary["pass"] > 0


# ---------------------------------------------------------------- defects: >=90% detected ---------------------
def test_defects_fixture_detects_at_least_90pct_of_seeded_defects(tmp_path):
    path, defect_ids = build_defects(tmp_path / "defects.docx")
    assert len(defect_ids) >= 8
    doc = parse_docx(path)
    report = checks_mod.run_checks(doc, TEMPLATE)
    problems = [f for f in report.findings if f.severity != "pass"]
    joined = " | ".join(f"{f.check}:{f.message}" for f in problems)

    detected = {
        "missing_section_zakljucak": "required section missing: zakljucak" in joined,
        "heading_hierarchy_gap": "heading_hierarchy" in " ".join(f.check for f in problems),
        "missing_english_abstract": "no abstract detected for language 'en'" in joined,
        "toc_mismatch": "TOC entry has no matching heading" in joined,
        "duplicate_figure_number": "duplicate figure number" in joined,
        "dangling_citation": "no matching reference entry" in joined,
        "uncited_reference": "never cited in text" in joined,
        "wrong_font_size": "font size is 11.0pt" in joined,
        "wrong_line_spacing": "line spacing is 1.0" in joined,
        "duplicate_heading_title": "heading title used" in joined,
    }
    hit_rate = sum(detected.values()) / len(detected)
    missed = [k for k, v in detected.items() if not v]
    assert hit_rate >= 0.9, f"only {hit_rate:.0%} of seeded defects detected; missed: {missed}"
    assert set(detected) == set(defect_ids)                   # the test's own ledger matches the fixture's


# ---------------------------------------------------------------- cyrillic -------------------------------------
def test_cyrillic_fixture_detects_script_and_matches_headings_across_script(tmp_path):
    path = build_cyrillic(tmp_path / "cyrillic.docx")
    doc = parse_docx(path)
    assert len(doc.abstracts) == 2
    sr_abstract = next(a for a in doc.abstracts if a.lang == "sr")
    assert sr_abstract.script == "cyrillic"
    en_abstract = next(a for a in doc.abstracts if a.lang == "en")
    assert en_abstract.script == "latin"

    report = checks_mod.run_checks(doc, TEMPLATE)
    fails = [f for f in report.findings if f.severity == "fail"]
    assert fails == [], f"unexpected failures on the clean Cyrillic fixture: {fails}"
    # the template's own match keywords are Latin-script; Cyrillic headings must still resolve via
    # Template.match_section_key's substring match against transliteration-free Cyrillic text - check the
    # required sections were actually found, not silently skipped because the script differs.
    required_findings = [f for f in report.findings if f.check == "required_sections"]
    missing = [f.message for f in required_findings if f.severity == "fail"]
    assert missing == [], f"Cyrillic headings failed to match the template: {missing}"


@pytest.mark.parametrize("check_fn_name", checks_mod.CHECK_IDS)
def test_every_check_id_is_exercised_by_the_clean_fixture(check_fn_name, tmp_path):
    path = build_clean(tmp_path / "clean.docx")
    doc = parse_docx(path)
    report = checks_mod.run_checks(doc, TEMPLATE)
    assert any(f.check == check_fn_name for f in report.findings), f"check '{check_fn_name}' produced no findings at all"
