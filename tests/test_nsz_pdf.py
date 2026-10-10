"""NSZ PDF pipeline on a SYNTHETIC PDF generated at test time (no real publication content committed)."""
import io
from datetime import date, datetime, timezone

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ajp.connectors import nsz_pdf
from ajp.connectors.nsz_pdf import BODY, EMPLOYER, TITLE, Issue, Line
from ajp.extract.text import is_oversized_or_listlike_title

NOW = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)
ISSUE = Issue("1214", date(2026, 9, 16), "https://www.nsz.gov.rs/filemanager/Files/PDF_Arhiva_poslova/2026/Poslovi%201214.pdf")
W, H = A4


def make_pdf(pages):
    """pages: list of {'section': str, 'cols': [[(style, text), ...] x3]}; style in emp/title/body."""
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    fonts = {"emp": ("Helvetica-Bold", 11), "title": ("Helvetica-Bold", 9.8), "body": ("Helvetica", 8.8)}
    for pg in pages:
        c.setFont("Helvetica-Bold", 9); c.drawString(30, H - 22, pg["section"])
        for ci, col in enumerate(pg["cols"]):
            y = H - 60
            for style, text in col:
                c.setFont(*fonts[style]); c.drawString(20 + ci * (W / 3), y, text); y -= 12
        c.setFont("Helvetica-Bold", 7); c.drawString(30, 14, "34 BROJ 1214 | 16.09.2026.")          # footer: must be ignored
        c.saveShow() if False else c.showPage()
    c.save()
    return buf.getvalue()


PAGES = [
    {"section": "Cover", "cols": [[("emp", "IGNORED COVER")], [], []]},
    {"section": "Cover", "cols": [[("emp", "IGNORED CONTENTS")], [], []]},
    {"section": "Nauka i obrazovanje", "cols": [
        [("emp", "FAKULTET PRIMER"), ("body", "21000 Novi Sad, Primerova 1"),
         ("title", "Docent za naucnu oblast Fizika"), ("body", "na odredjeno vreme, na 5 godina"),
         ("body", "USLOVI: doktorat iz oblasti fizike."), ("body", "Rok za podnosenje")],
        [("body", "prijave je 15 dana od dana"), ("body", "objavljivanja. Prijave se podnose"), ("body", "na adresu."),
         ("emp", "OSNOVNA SKOLA PRIMER"), ("body", "11000 Beograd, Skolska 2"),
         ("title", "Nastavnik matematike"), ("body", "USLOVI: master. Rok za podnosenje"),
         ("body", "prijave je 8 dana od dana"), ("body", "objavljivanja.")],
        [("emp", "INSTITUT ZA FIZIKU PRIMER"), ("body", "11080 Zemun, Institutska 3"),
         ("title", "Istrazivac-saradnik"), ("body", "USLOVI: master akademske studije.")]]},
    {"section": "Nauka i obrazovanje", "cols": [
        [("body", "Rok za podnosenje prijave je 30"), ("body", "dana od dana objavljivanja."),
         ("emp", "UNIVERZITET PRIMER"), ("body", "18000 Nis, Trg 1"), ("title", "Asistent"),
         ("body", "USLOVI: student doktorskih studija.")], [], []]},
]


@pytest.fixture(scope="module")
def pdf_bytes():
    return make_pdf(PAGES)


def test_lines_are_column_ordered_classified_and_noise_is_dropped(pdf_bytes):
    lines = list(nsz_pdf.iter_lines(pdf_bytes))
    texts = [l.text for l in lines]
    assert not any("IGNORED" in t or "BROJ 1214" in t for t in texts)          # cover pages + footer excluded
    p3 = [l for l in lines if l.page == 3]
    assert [l.col for l in p3] == sorted(l.col for l in p3)                   # column 1, then 2, then 3
    kinds = {l.text: l.cls for l in p3}
    assert kinds["FAKULTET PRIMER"] == EMPLOYER and kinds["Docent za naucnu oblast Fizika"] == TITLE
    assert kinds["21000 Novi Sad, Primerova 1"] == BODY
    assert all(l.section == "Nauka i obrazovanje" for l in p3)


def test_segmentation_joins_ads_across_columns_and_pages(pdf_bytes):
    ads = nsz_pdf.segment(list(nsz_pdf.iter_lines(pdf_bytes)))
    assert [(a.employer, a.title) for a in ads] == [
        ("FAKULTET PRIMER", "Docent za naucnu oblast Fizika"), ("OSNOVNA SKOLA PRIMER", "Nastavnik matematike"),
        ("INSTITUT ZA FIZIKU PRIMER", "Istrazivac-saradnik"), ("UNIVERZITET PRIMER", "Asistent")]
    docent = ads[0]
    assert docent.address == "21000 Novi Sad, Primerova 1"
    assert "Rok za podnosenje prijave je 15 dana od dana objavljivanja" in docent.body     # sentence split over a column break
    inst = ads[2]
    assert inst.page_first == 3 and inst.page_last == 4 and "30 dana" in inst.body          # continued on the next page


def test_soft_hyphen_and_wrapping():
    assert nsz_pdf._join(["neko­", "liko reci", "i jos"]) == "nekoliko reci i jos"
    assert nsz_pdf._join(["a", " b  ", "c"]) == "a b c"


def test_only_academic_ads_become_rows_with_stated_facts_only(pdf_bytes):
    ads = nsz_pdf.segment(list(nsz_pdf.iter_lines(pdf_bytes)))
    rows, stats = nsz_pdf.rows_from_ads(ads, ISSUE, "raw/key", now=NOW)
    assert stats == {"ads_segmented": 4, "positions": 4, "academic": 3, "excluded_non_academic": 1}      # primary school teacher dropped
    by_title = {r["title_raw"]: r for r in rows}
    d = by_title["Docent za naucnu oblast Fizika"]
    assert (d["position_kind"], d["kind"], d["degree_required"], d["contract"], d["city"]) == ("professor", "employment", "phd", "fixed", "Novi Sad")
    assert (d["duration_months_v"], d["duration_months_raw"]) == (60, "na 5 godina")
    assert d["deadline"] == datetime(2026, 10, 1, 21, 59, 59, tzinfo=timezone.utc) and d["status"] == "open"      # 16.09 + 15 d
    assert d["url"].endswith("Poslovi%201214.pdf#page=3") and d["posted_at"].date() == date(2026, 9, 16)
    r = by_title["Istrazivac-saradnik"]
    assert (r["position_kind"], r["rs_research_title"], r["degree_required"], r["deadline"].date()) == \
        ("researcher_title", "istrazivac_saradnik", "msc", date(2026, 10, 16))                                     # rule sentence on page 4
    for row in rows:
        assert not any(k.startswith("salary") for k in row) and row["raw_text"] and row["ext_uid"].startswith("2026-1214-")
    assert len({r["ext_uid"] for r in rows}) == 3


def test_rows_are_idempotent_across_reruns(pdf_bytes):
    ads = nsz_pdf.segment(list(nsz_pdf.iter_lines(pdf_bytes)))
    a, _ = nsz_pdf.rows_from_ads(ads, ISSUE, "k", now=NOW); b, _ = nsz_pdf.rows_from_ads(ads, ISSUE, "k", now=NOW)
    assert [r["ext_uid"] for r in a] == [r["ext_uid"] for r in b]


def test_list_issues_parses_index_and_ignores_other_links():
    html = ('<a href="https://www.nsz.gov.rs/filemanager/Files/PDF_Arhiva_poslova/2026/Poslovi%201214.pdf">Број 1214 - 16.09.2026. године</a>'
            '<a href="/filemanager/Files/PDF_Arhiva_poslova/2026/1204-1205.pdf">Број 1204-1205 - 08.07.2026. године</a>'
            '<a href="/filemanager/Files/Dokumenta/x.xls">Број запослених</a><a href="https://evil.example/a.pdf">Број 1 - 01.01.2026.</a>')
    issues = nsz_pdf.list_issues(html)
    assert [(i.label, i.date.isoformat()) for i in issues] == [("1214", "2026-09-16"), ("1204-1205", "2026-07-08")]
    assert issues[1].url == "https://www.nsz.gov.rs/filemanager/Files/PDF_Arhiva_poslova/2026/1204-1205.pdf"


def test_rows_ingest_into_db(conn, tmp_path, pdf_bytes):
    from ajp.connectors.nsz import NszConnector
    from ajp.ingest import upsert_listing
    from ajp.rawstore import LocalFsStore, put_raw
    store = LocalFsStore(tmp_path)
    ref = put_raw(store, kind="pdf", source_id="nsz", url=ISSUE.url, content=pdf_bytes, fetched_at=NOW, status=200,
                  content_type="application/pdf", robots_allowed=True)
    NszConnector.ensure_source(NszConnector.__new__(NszConnector), conn)
    rows, _ = nsz_pdf.rows_from_ads(nsz_pdf.segment(list(nsz_pdf.iter_lines(pdf_bytes))), ISSUE, ref.key, now=NOW)
    assert all(upsert_listing(conn, r, store)[1] for r in rows)
    assert all(not upsert_listing(conn, r, store)[1] for r in rows)                  # rerun updates, no duplicates
    assert conn.execute("SELECT count(*), count(DISTINCT ext_uid) FROM listing").fetchone() == (3, 3)


# ---- behaviours learned from the first live run (wording modelled on real ads, text invented) ------------
def test_one_block_with_several_positions_becomes_one_row_each():
    title = ("Asistent za uzu naucnu oblast Mehanika na odredjeno vreme od 3 godine Asistent za uzu naucnu oblast "
             "Fizika na odredjeno vreme od 3 godine Vanredni ili redovni profesor za oblast Hemija")
    assert nsz_pdf.split_titles(title) == [
        "Asistent za uzu naucnu oblast Mehanika na odredjeno vreme od 3 godine",
        "Asistent za uzu naucnu oblast Fizika na odredjeno vreme od 3 godine",
        "Vanredni ili redovni profesor za oblast Hemija"]
    assert nsz_pdf.split_titles("Ванредни или редовни професор за област Х") == ["Ванредни или редовни професор за област Х"]
    assert nsz_pdf.split_titles("Docent za oblast X, u Institutu za Y") == ["Docent za oblast X, u Institutu za Y"]
    block = nsz_pdf.PdfAd("UNIVERZITET U BEOGRADU MASINSKI FAKULTET", "", title, "USLOVI: doktorat. Prijave u roku od 15 dana od dana objavljivanja.", 5, 5, "S")
    rows, stats = nsz_pdf.rows_from_ads([block], ISSUE, "k", now=NOW)
    assert stats["positions"] == 3 and len(rows) == 3 and len({r["ext_uid"] for r in rows}) == 3
    assert all(r["deadline"].date() == date(2026, 10, 1) and r["degree_required"] == "phd" for r in rows)   # shared conditions
    assert (rows[0]["duration_months_v"], rows[0]["duration_months_raw"]) == (36, "vreme od 3 godine")
    assert "duration_months_v" not in rows[2]
    assert rows[0]["apply_url"] == rows[0]["url"]                              # T26 (packet 015)


def test_f10_a_real_numbered_lecturer_list_from_a_real_live_issue_splits_into_one_title_each():
    """T35b (packet 021, PM-verified defect, a real live issue-1216 row): 'Предавач' (Lecturer, a standard
    rank at vocational academies) was missing from _RANKS entirely, so a real numbered run of several such
    positions never split at all - the whole ~700-char numbered list became ONE oversized title. Real text,
    trimmed to 3 of the original 6+ numbered positions."""
    title = ("3. Предавач за област Медицина, ужа научна односно стручна област Радиологија са 5% радног "
            "времена, на одређено време од 5 година у Одсеку медицинских студија Ћуприја "
            "4. Предавач за област Медицина, ужа научна односно стручна област Хирургија са 5% радног "
            "времена, на одређено време од 5 година у Одсеку медицинских студија Ћуприја "
            "5. Предавач за област Медицина, ужа научна односно стручна област Психијатрија")
    parts = nsz_pdf.split_titles(title)
    assert len(parts) == 3
    assert all(p.startswith(("3. Предавач", "4. Предавач", "5. Предавач")) for p in parts)
    assert all(not is_oversized_or_listlike_title(p) for p in parts)


def test_f10_a_still_oversized_title_that_somehow_evades_split_titles_falls_back_never_stored_raw():
    """Defensive backstop (T35b): even if some future real block shape evades split_titles' own fix, _row()
    itself must never store a raw oversized/list-like wall of text as title_raw. A real rank word starts
    it (so academic_ad still accepts the row) followed by many sentences with no FURTHER rank word at all -
    split_titles finds no additional split point, so the whole thing stays one oversized segment."""
    huge_unsplittable = "Доцент за област Физика. " + ("Ово је веома дугачак текст конкурса. " * 20)
    assert nsz_pdf.split_titles(huge_unsplittable) == [huge_unsplittable.strip()]   # confirms it truly evades the split
    block = nsz_pdf.PdfAd("NEKI FAKULTET", "", huge_unsplittable, "USLOVI: doktorat.", 1, 1, "S")
    rows, _ = nsz_pdf.rows_from_ads([block], ISSUE, "k", now=NOW)
    assert len(rows) == 1
    # the fallback recovers the real short FIRST sentence here (a genuinely better outcome than the generic
    # employer label, which is reserved for when even the first sentence is itself still too long/list-like)
    assert rows[0]["title_raw"] == "Доцент за област Физика"
    assert not is_oversized_or_listlike_title(rows[0]["title_raw"])


def test_f10_when_even_the_first_sentence_is_still_oversized_the_generic_employer_label_is_used():
    huge_first_sentence = "Доцент за област Физика " + ("са веома дугим и нечитким описом позиције " * 20) + "."
    assert is_oversized_or_listlike_title(huge_first_sentence.split(".")[0].strip())
    block = nsz_pdf.PdfAd("NEKI FAKULTET", "", huge_first_sentence, "USLOVI: doktorat.", 1, 1, "S")
    rows, _ = nsz_pdf.rows_from_ads([block], ISSUE, "k", now=NOW)
    assert len(rows) == 1
    assert rows[0]["title_raw"] == "Position(s) announced by NEKI FAKULTET"


def test_a_plain_email_in_the_pdf_s_own_extracted_text_is_a_real_contact():
    """PDF text extraction has no HTML markup at all (no mailto: links) - only the plain-text email/phone
    patterns ever apply here, never the HTML-only ones."""
    block = nsz_pdf.PdfAd("UNIVERZITET U NISU", "", "Docent za oblast X",
                          "USLOVI: doktorat. Prijave: kadrovi@ni.ac.rs.", 1, 1, "S")
    rows, _ = nsz_pdf.rows_from_ads([block], ISSUE, "k", now=NOW)
    assert rows[0]["contact_emails"] == ["kadrovi@ni.ac.rs"]


def test_non_academic_roles_at_academic_employers_are_not_rows():
    blocks = [nsz_pdf.PdfAd("UNIVERZITET U BEOGRADU MEDICINSKI FAKULTET", "", "Cistacica na odredjeno vreme do 6 meseci", "", 1, 1, "S"),
              nsz_pdf.PdfAd("OSNOVNA SKOLA X", "", "Pedagoski (romski) asistent na odredjeno vreme", "", 1, 1, "S"),
              nsz_pdf.PdfAd("UNIVERZITETSKI KLINICKI CENTAR", "", "Referent za finansije", "", 1, 1, "S")]
    rows, stats = nsz_pdf.rows_from_ads(blocks, ISSUE, "k", now=NOW)
    assert rows == [] and stats["excluded_non_academic"] == 3


# ---- pm advisory (T6 pdf): enumerate hrefs from the index; names vary; numbers reset every year -------------
def test_index_variants_and_yearly_number_reset():
    base = "https://www.nsz.gov.rs/filemanager/Files/PDF_Arhiva_poslova"
    html = "".join(f'<a href="{href}">{label}</a>' for href, label in [
        (f"{base}/2026/Poslovi%201214.pdf", "Број 1214 - 16.09.2026. године"),
        (f"{base}/2026/Poslovi%2C%201213.pdf", "Број 1213 - 09.09.2026. године"),                # comma variant
        (f"{base}/2026/Poslovi%2C%201210-1212.pdf", "Број 1210-1212 - 19.08.2026. године"),      # range
        (f"/filemanager/Files/PDF_Arhiva_poslova/2026/1204-1205.pdf", "Број 1204-1205 - 08.07.2026. године"),   # bare, relative
        (f"{base}/2019/Poslovi%201214.pdf", "Број 1214 - 20.03.2019. године"),                    # same number, older year
    ])
    issues = nsz_pdf.list_issues(html)
    assert [(i.label, i.date.year) for i in issues] == [("1214", 2026), ("1213", 2026), ("1210-1212", 2026), ("1204-1205", 2026), ("1214", 2019)]
    assert issues[3].url == base + "/2026/1204-1205.pdf"
    old, new = issues[4], issues[0]                       # 2019 vs 2026, same number 1214
    block = nsz_pdf.PdfAd("UNIVERZITET U NISU", "", "Docent za oblast X", "USLOVI: doktorat.", 1, 1, "S")
    (r_new,), _ = nsz_pdf.rows_from_ads([block], new, "k", now=NOW)
    (r_old,), _ = nsz_pdf.rows_from_ads([block], old, "k", now=NOW)
    assert r_new["ext_uid"] != r_old["ext_uid"] and r_new["ext_uid"].startswith("2026-1214-") and r_old["ext_uid"].startswith("2019-1214-")


class _OfflinePdf:
    """NszConnector wired to canned responses (no network) to test backfill bounds."""
    @staticmethod
    def make(tmp_path, index_html, pdf_bytes, content_type="application/pdf"):
        from ajp.connectors import nsz
        from ajp.connectors.base import FetchResult
        from ajp.rawstore import LocalFsStore, put_raw
        class C(nsz.NszConnector):
            fetched = []
            def fetch_raw(self, url, kind="html", max_bytes=None):
                C.fetched.append(url)
                body = index_html.encode() if url == nsz.PDF_INDEX else pdf_bytes
                ct = "text/html" if url == nsz.PDF_INDEX else content_type
                res = FetchResult(url, url, 200, body, ct, NOW)
                return res, put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                                    status=200, content_type=ct, robots_allowed=True)
        from ajp.connectors.base import PoliteClient
        slept = []
        c = C(PoliteClient(rps=1000, sleep=slept.append), LocalFsStore(tmp_path))
        return c, C, slept


INDEX = "".join(f'<a href="https://www.nsz.gov.rs/filemanager/Files/PDF_Arhiva_poslova/{y}/Poslovi%20{n}.pdf">Број {n} - {d} године</a>'
                for y, n, d in [(2026, 1214, "16.09.2026."), (2026, 1213, "09.09.2026."), (2025, 1160, "10.12.2025."), (2024, 1100, "01.03.2024.")])


def test_backfill_defaults_to_current_year_only_and_spaces_pdfs(tmp_path, pdf_bytes):
    c, cls, slept = _OfflinePdf.make(tmp_path, INDEX, pdf_bytes)
    out = list(c.crawl_pdf_backfill(years={2026}))
    assert [i.label for i, _, _ in out] == ["1214", "1213"]
    assert [u.rsplit("/", 1)[1] for u in cls.fetched if u.endswith(".pdf")] == ["Poslovi%201214.pdf", "Poslovi%201213.pdf"]   # 2025/2024 untouched
    assert 2.0 in slept and slept.count(2.0) == 1                                                                             # 2 s between PDFs
    out = list(c.crawl_pdf_backfill(years={2025, 2024}, max_issues=1))                                                        # explicit opt-in, bounded
    assert [i.label for i, _, _ in out] == ["1160"]


def test_backfill_rejects_non_pdf_and_reports_errors_per_issue(tmp_path, pdf_bytes):
    c, cls, _ = _OfflinePdf.make(tmp_path, INDEX, b"<html>blocked</html>", content_type="text/html")
    (issue, rows, stats), *_ = list(c.crawl_pdf_backfill(years={2026}))
    assert rows == [] and "content-type" in stats["error"]
