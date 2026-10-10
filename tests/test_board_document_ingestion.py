"""T31 (packet 019): board.py's own document (.pdf/.docx/.doc) text ingestion - real fixtures, PM's own
ground-truth files captured live from real rs_org_boards documents (see tests/fixtures/documents/)."""
from datetime import datetime, timezone
from pathlib import Path

from ajp.connectors import board
from ajp.connectors.base import FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
ORG = {"id": None, "slug": "bg-example", "name_en": "Faculty Example", "name_native": "ПРИМЕР ФАКУЛТЕТ",
      "city": "Belgrade", "homepage": "https://www.example.invalid/",
      "career_urls": ["https://www.example.invalid/sr/konkursi"]}


class OfflineDoc(board.BoardConnector):
    bodies = {}   # url -> (bytes, content_type)

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        if url not in self.bodies:
            raise RuntimeError("HTTP 404 (canned)")
        body, ctype = self.bodies[url]
        res = FetchResult(url, url, 200, body, ctype, NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=200, content_type=ctype, robots_allowed=True)
        return res, ref


def _row(url, title, extraction_family="document", kind="employment"):
    return {"url": url, "title_raw": title, "extraction_family": extraction_family, "kind": kind,
           "raw_text": title, "ext_uid": "x", "source_id": "rs_org_boards", "position_kind": None,
           "org_id": None, "org_raw": "Faculty", "country_iso2": "RS", "ad_lang": "sr",
           "foreigner_eligible": "unknown", "apply_url": url, "raw_html_ref": "raw/key"}


def _connector(tmp_path):
    return OfflineDoc(PoliteClient(rps=1000, sleep=lambda s: None), LocalFsStore(tmp_path), ORG)


def test_a_real_text_pdf_document_row_gets_its_body_text_and_contacts_extracted(tmp_path):
    """PM's own ground truth (matf.bg.ac.rs, Konkurs-31.08.2026.pdf): phone 011/2027-819 in the real body."""
    url = "https://www.example.invalid/wp-content/uploads/2026/08/Konkurs-31.08.2026.pdf"
    OfflineDoc.bodies = {url: ((FIXTURES / "matf_konkurs_text.pdf").read_bytes(), "application/pdf")}
    c = _connector(tmp_path)
    rows = [_row(url, "Конкурс за избор доцента")]
    report = {}
    c._enrich_documents(rows, report)
    row = rows[0]
    assert report["documents_attempted"] == 1 and report["documents_extracted"] == 1
    assert row["content_scanned"] is False
    assert "011/2027-819" in row["raw_text"]
    assert row["contact_phones"] == ["011/2027-819"]
    assert row["requirements_raw"] == row["raw_text"]


def test_a_real_scanned_pdf_document_row_is_flagged_content_scanned_never_dropped(tmp_path):
    """PM's own ground truth (medf.kg.ac.rs, Konkursi_od_24062026_POSLOVI.pdf): 4 pages, 0 extractable
    chars - a genuinely scanned document. Must never be dropped, and the original listing-page text must
    never be silently overwritten with nothing (OCR stays out of scope, D6)."""
    url = "https://www.example.invalid/wp-content/uploads/2026/06/Konkursi_od_24062026_POSLOVI.pdf"
    OfflineDoc.bodies = {url: ((FIXTURES / "medf_konkurs_scanned.pdf").read_bytes(), "application/pdf")}
    c = _connector(tmp_path)
    rows = [_row(url, "Конкурс за избор")]
    report = {}
    c._enrich_documents(rows, report)
    row = rows[0]
    assert report["documents_scanned"] == 1 and report["documents_extracted"] == 0
    assert row["content_scanned"] is True
    assert row["raw_text"] == "Конкурс за избор"          # unchanged - never overwritten with an empty extraction


def test_a_real_docx_document_row_gets_its_body_text_and_contacts_extracted(tmp_path):
    """PM's own ground truth (ekof.bg.ac.rs, Konkurs-EF48.docx): mobile 069/80-66-390 in the real body."""
    url = "https://www.example.invalid/wp-content/uploads/2019/03/Konkurs-EF48.docx"
    OfflineDoc.bodies = {url: ((FIXTURES / "ekof_konkurs.docx").read_bytes(),
                              "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    c = _connector(tmp_path)
    rows = [_row(url, "Конкурс за избор")]
    report = {}
    c._enrich_documents(rows, report)
    row = rows[0]
    assert report["documents_extracted"] == 1
    assert "069/80-66-390" in row["raw_text"]
    assert row["contact_phones"] == ["069/80-66-390"]


def test_a_failed_document_fetch_never_drops_the_row(tmp_path):
    url = "https://www.example.invalid/wp-content/uploads/broken.pdf"
    OfflineDoc.bodies = {}
    c = _connector(tmp_path)
    rows = [_row(url, "Конкурс за избор")]
    report = {}
    c._enrich_documents(rows, report)
    assert len(rows) == 1                                  # never dropped
    assert report["documents_failed"] == 1
    assert "content_scanned" not in rows[0]                # never guessed when the fetch itself failed


def test_a_corrupt_document_that_fetches_fine_but_cannot_be_parsed_never_drops_the_row(tmp_path):
    url = "https://www.example.invalid/wp-content/uploads/corrupt.pdf"
    OfflineDoc.bodies = {url: (b"not actually a pdf, just garbage bytes", "application/pdf")}
    c = _connector(tmp_path)
    rows = [_row(url, "Конкурс за избор")]
    report = {}
    c._enrich_documents(rows, report)
    assert len(rows) == 1
    assert report["documents_failed"] == 1
    assert "content_scanned" not in rows[0]


def test_non_document_family_and_non_employment_rows_are_never_attempted(tmp_path):
    """A 'document'-family fetch is only ever attempted for an employment row - the same guard every other
    enrichment pass in this module already applies."""
    c = _connector(tmp_path)
    rows = [_row("https://x.invalid/a.pdf", "t", extraction_family="article"),
           _row("https://x.invalid/b.pdf", "t", kind="grant_call")]
    report = {}
    c._enrich_documents(rows, report)
    assert report["documents_attempted"] == 0


def test_an_unrecognised_extension_is_never_attempted(tmp_path):
    c = _connector(tmp_path)
    rows = [_row("https://www.example.invalid/x.xlsx", "t")]
    report = {}
    c._enrich_documents(rows, report)
    assert report["documents_attempted"] == 0


def test_title_derived_contract_is_never_overwritten_by_the_document_body(tmp_path):
    """rows_from_items (listing-title-only) may have already set contract/duration from the title itself -
    the real DOCX fixture's own body states a DIFFERENT contract type ('на одређено време', fixed-term,
    per PM's own recon) - the document body must only ever ADD what the title alone could not, never
    overwrite an already-set, already-validated value."""
    from ajp.extract import rs_text
    url = "https://www.example.invalid/wp-content/uploads/2019/03/Konkurs-EF48.docx"
    OfflineDoc.bodies = {url: ((FIXTURES / "ekof_konkurs.docx").read_bytes(),
                              "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    c = _connector(tmp_path)
    row = _row(url, "Конкурс за избор")
    row["contract"] = "permanent"                            # pretend the title already stated this
    report = {}
    c._enrich_documents([row], report)
    assert rs_text.contract_type(row["raw_text"])["v"] != "permanent"   # sanity: the body really does disagree
    assert row["contract"] == "permanent"                    # untouched by the document body
