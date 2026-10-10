"""T31 (packet 019): PDF/DOCX/legacy-DOC text extraction for board.py's own document-linked konkurs rows -
real fixtures, PM's own ground-truth files (captured live from real rs_org_boards documents, 2026-09-28)."""
from pathlib import Path

import pytest

from ajp.extract import document as ed

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


# ---- T34 (packet 018/020, PM-verified defect): a broken ToUnicode CMap can produce a LONG string that
# clears the bare character-count floor while being mostly U+FFFD - a second, independent scanned signal. --
def test_a_long_but_mostly_fffd_string_is_treated_as_scanned_even_though_it_clears_the_char_count_floor():
    garbage = "� " * 100   # 200 chars total, well above SCANNED_MIN_CHARS, but almost no real text
    assert ed._looks_scanned_or_garbled(garbage) is True


def test_a_long_string_with_too_few_real_alphabetic_characters_is_also_treated_as_scanned():
    mostly_digits_and_punctuation = "123.456,789 " * 20   # 240 chars, 0 alphabetic characters
    assert ed._looks_scanned_or_garbled(mostly_digits_and_punctuation) is True


def test_real_extracted_text_with_a_handful_of_incidental_fffd_chars_is_never_flagged_scanned():
    """The 5% ratio guard must not overreach - a real, long, genuinely-extracted document with just one or
    two incidental bad characters (a font glyph pdfplumber could not map, common in real PDFs) must still be
    treated as real text, not scanned."""
    real_text = ("Конкурс за избор у звање доцента за ужу научну област Рачунарство и информатика. " * 3
                + "�")
    assert ed._looks_scanned_or_garbled(real_text) is False


def test_a_real_text_extractable_pdf_yields_its_real_body_text():
    """PM's own ground truth (matf.bg.ac.rs, Konkurs-31.08.2026.pdf): 1162 chars, phone 011/2027-819."""
    r = ed.extract_pdf_text(FIXTURES / "matf_konkurs_text.pdf")
    assert not r.extract_failed and not r.content_scanned
    assert r.page_count == 1
    assert len(r.text) == 1162
    assert "011/2027-819" in r.text
    assert "МАТЕМАТИЧКИ ФАКУЛТЕТ" in r.text


def test_a_real_scanned_image_only_pdf_is_flagged_content_scanned_never_extract_failed():
    """PM's own ground truth (medf.kg.ac.rs, Konkursi_od_24062026_POSLOVI.pdf): 4 pages, 0 extractable
    chars - a real scanned document, not a corrupt file. OCR stays explicitly out of scope (D6) - this must
    never crash or be treated as a failure, only honestly flagged."""
    r = ed.extract_pdf_text(FIXTURES / "medf_konkurs_scanned.pdf")
    assert not r.extract_failed
    assert r.content_scanned is True
    assert r.page_count == 4
    assert r.text == ""


def test_a_real_docx_yields_its_real_body_text():
    """PM's own ground truth (ekof.bg.ac.rs, Konkurs-EF48.docx): mobile 069/80-66-390, duration text."""
    r = ed.extract_docx_text(FIXTURES / "ekof_konkurs.docx")
    assert not r.extract_failed and not r.content_scanned
    assert "069/80-66-390" in r.text
    assert "одређено време" in r.text
    assert "пуним радним временом" in r.text


def test_extract_document_text_dispatches_on_suffix():
    r_pdf = ed.extract_document_text(FIXTURES / "matf_konkurs_text.pdf", ".pdf")
    r_docx = ed.extract_document_text(FIXTURES / "ekof_konkurs.docx", ".docx")
    assert r_pdf.text and r_docx.text


def test_a_corrupt_pdf_is_extract_failed_never_raises(tmp_path):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"not actually a pdf at all, just garbage bytes")
    r = ed.extract_pdf_text(bad)
    assert r.extract_failed is True
    assert r.text == "" and r.failure_reason


def test_a_corrupt_docx_is_extract_failed_never_raises(tmp_path):
    bad = tmp_path / "corrupt.docx"
    bad.write_bytes(b"not a real docx zip package")
    r = ed.extract_docx_text(bad)
    assert r.extract_failed is True
    assert r.text == "" and r.failure_reason


def test_a_missing_file_is_extract_failed_never_raises(tmp_path):
    r = ed.extract_pdf_text(tmp_path / "does-not-exist.pdf")
    assert r.extract_failed is True


def test_legacy_doc_without_textutil_available_is_honestly_flagged_not_guessed(monkeypatch, tmp_path):
    monkeypatch.setattr(ed.shutil, "which", lambda name: None)
    r = ed.extract_legacy_doc_text(tmp_path / "old.doc")
    assert r.extract_failed is True
    assert "textutil" in r.failure_reason


@pytest.mark.skipif(ed.shutil.which("textutil") is None, reason="textutil is a macOS-only system tool")
def test_legacy_doc_with_a_real_missing_file_and_textutil_available_still_fails_honestly(tmp_path):
    """textutil IS available on this host but the file itself does not exist - must still fail honestly,
    never crash the crawl."""
    r = ed.extract_legacy_doc_text(tmp_path / "does-not-exist.doc")
    assert r.extract_failed is True


def test_an_unsupported_suffix_is_extract_failed_with_a_clear_reason():
    r = ed.extract_document_text("/tmp/whatever.xlsx", ".xlsx")
    assert r.extract_failed is True
    assert "xlsx" in r.failure_reason
