"""NSZ (Nacionalna služba za zapošljavanje) job-board connector, HTML part of T6.

Sections and detail pages are public and robots-allowed (verified 2026-09-22). All requests go through
PoliteClient with the permitted-URL manifest below; the weekly PDF archive is the next slice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, UrlManifest
from ajp.connectors.jobposting import classify_location
from ajp.extract import rs_text
from ajp.extract.text import decode_bytes, is_oversized_or_listlike_title
from ajp.extract_contacts import extract_contacts
from ajp.robots import RobotsDenied

BASE = "https://www.nsz.gov.rs"
SECTIONS = {"poslovi": "/sadrzaji/poslovi/279", "konkursi": "/sadrzaji/konkursi/157",
            "nzs-konkursi": "/sadrzaji/nzs-konkursi/10", "ostali-konkursi": "/sadrzaji/ostali-konkursi/277"}
MANIFEST = UrlManifest(
    r"https://www\.nsz\.gov\.rs/sadrzaji/(?:poslovi/279|konkursi/157|nzs-konkursi/10|ostali-konkursi/277)(?:\?page=\d{1,4})?",
    r"https://www\.nsz\.gov\.rs/sadrzaj/[^/?#\s]+/\d+",
    r"https://www\.nsz\.gov\.rs/sadrzaj/pdf-arhiva-poslova/4108",
    r"https://www\.nsz\.gov\.rs/filemanager/Files/PDF_Arhiva_poslova/\d{4}/[^?#\s]+\.pdf",
)
PDF_INDEX = "https://www.nsz.gov.rs/sadrzaj/pdf-arhiva-poslova/4108"
_DETAIL_HREF = re.compile(r"^https?://www\.nsz\.gov\.rs/sadrzaj/[^/?#\s]+/(\d+)$")
_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_BELGRADE = ZoneInfo("Europe/Belgrade")


def parse_date(s: str) -> Optional[date]:
    m = _DATE.search(s or "")
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
    except ValueError:
        return None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


@dataclass
class Card:
    url: str
    ext_id: str
    title: str
    category: str
    posted: Optional[date]
    teaser: str


def parse_listing(html: str, page_url: str = BASE) -> List[Card]:
    out = []
    for art in HTMLParser(html).css("article.news-item"):
        a = art.css_first("h2.news-item-title a") or art.css_first("a[href]")
        if not a:
            continue
        url = urljoin(page_url, a.attributes.get("href", ""))
        m = _DETAIL_HREF.match(url)
        if not m:
            continue
        cat = art.css_first(".news-item-category")
        out.append(Card(url=url, ext_id=m.group(1), title=_clean(a.text()), category=_clean(cat.text()) if cat else "",
                        posted=parse_date(art.css_first(".time-published").text() if art.css_first(".time-published") else ""),
                        teaser=_clean(art.css_first("p.content").text()) if art.css_first("p.content") else ""))
    return out


def last_page(html: str) -> int:
    pages = [int(m.group(1)) for a in HTMLParser(html).css("a") for m in [re.search(r"[?&]page=(\d+)", a.attributes.get("href") or "")] if m]
    return max(pages) if pages else 1


@dataclass
class Ad:
    position: str          # first line of the position paragraph (used as the title)
    body: str              # whole position paragraph
    conditions: str        # the УСЛОВИ / USLOVI paragraph(s), incl. the deadline sentence


@dataclass
class Detail:
    employer: str
    posted: Optional[date]
    address: Optional[str]
    ads: List[Ad] = field(default_factory=list)
    text: str = ""


_COND = re.compile(r"^\s*(услови|uslovi)\s*:", re.I)


def parse_detail(html: str) -> Detail:
    t = HTMLParser(html)
    h1 = t.css_first(".lead-title h1") or t.css_first("h1")
    box = t.css_first(".content-box")
    posted = parse_date(t.css_first(".single-content, .lead-single").text() if t.css_first(".lead-single") else "")
    if t.css_first(".lead-single .time-published"):
        posted = parse_date(t.css_first(".lead-single .time-published").text())
    paras = []
    for p in (box.css("p") if box else []):
        lines = [_clean(x) for x in re.split(r"<br\s*/?>", p.html or "") if _clean(HTMLParser(x).text())]
        lines = [_clean(HTMLParser("<x>" + x + "</x>").text()) for x in lines]
        if lines:
            paras.append(lines)
    d = Detail(employer=_clean(h1.text()) if h1 else "", posted=posted, address=None,
               text=_clean(box.text(separator=" ")) if box else "")
    if paras and re.match(r"^\d{5}\s", paras[0][-1]):
        d.address = paras[0][-1]
    i = 1 if d.address else 0
    while i < len(paras):
        if _COND.match(paras[i][0]):
            i += 1
            continue
        nxt = paras[i + 1] if i + 1 < len(paras) else None
        if nxt and _COND.match(nxt[0]):
            d.ads.append(Ad(position=paras[i][0], body=" ".join(paras[i]), conditions=" ".join(nxt)))
            i += 2
        else:
            i += 1
    return d


def city_from_address(addr: Optional[str]) -> Optional[str]:
    m = re.match(r"^\d{5}\s+([^,]+)", addr or "")
    return m.group(1).strip() if m else None


# T35b (packet 021, PM-verified defect, 12 more real open rows on top of medf.kg.ac.rs's own 12): a formal
# government ministry "ЈАВНИ КОНКУРС" notice's own paragraph structure does not match the simpler single-
# employer NSZ ad template this parser's own `paras[i][0]` heuristic was built for - for these real pages,
# what ends up in `ad.position` (meant to be the FIRST LINE of the position paragraph) is instead the WHOLE
# "Опис послова: ..." duties paragraph (no real `<br>`-separated short title line exists to split it from at
# all). Never a filename to fall back to here (unlike board.py's own document links) - falls back to the
# position text's own first sentence, then a generic label naming the real employer, never the raw wall of
# text.
def _short_position_title(position: str, employer: str) -> str:
    if not is_oversized_or_listlike_title(position):
        return position
    first_sentence = re.split(r"[.!?]\s|\n", position, maxsplit=1)[0].strip()
    if first_sentence and not is_oversized_or_listlike_title(first_sentence):
        return first_sentence
    return f"Positions announced by {employer}" if employer else "Untitled position notice"


def rows_from_detail(card: Card, det: Detail, raw_ref: str, source_id: str = "nsz", now: Optional[datetime] = None,
                     html: str = "") -> List[Dict[str, Any]]:
    """Listing rows for one employer page. Absent facts stay absent (D6); every extracted value carries its raw span.
    `html` (T26, packet 015): the employer page's own raw markup, for mailto:/tel: link extraction - optional
    only so existing direct unit tests that build a bare Detail without ever fetching a page don't need to change."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for n, ad in enumerate(det.ads, 1):
        full = f"{ad.body} {ad.conditions}"
        kind = rs_text.classify_kind(f"{ad.position} {det.employer}")   # judged on the title, not the conditions text
        if kind == "other" and card.category:
            kind = "employment"                                     # an entry in the job board is a job ad...
        if kind == "employment" and not rs_text.academic_ad(ad.position, det.employer):
            kind = "other"                                          # ...but only academic ones are in the default view
        pk, rs_title = rs_text.position_kind(ad.position)
        location = classify_location(full, has_country=True)   # T23 (PM ruling R2) completeness fix
        contacts = extract_contacts(html=html, text=full)
        # T35b: classification above is judged on the FULL ad.position text (unchanged) - only what gets
        # STORED as title_raw is ever shortened, never the signal the kind/position_kind classifiers see.
        title_raw = _short_position_title(ad.position, det.employer)
        row: Dict[str, Any] = {
            "ext_uid": card.ext_id if len(det.ads) == 1 else f"{card.ext_id}-{n}", "source_id": source_id, "url": card.url,
            "title_raw": title_raw, "org_raw": det.employer, "country_iso2": "RS", "city": city_from_address(det.address),
            "position_kind": pk, "ad_lang": rs_text.script_of(full), "raw_text": full, "raw_html_ref": raw_ref,
            "kind": kind,
            "requirements_raw": ad.conditions, "foreigner_eligible": "unknown", "extraction_family": "nsz_html",
            "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
            "location_class_raw": location["location_class_raw"],
            "apply_url": card.url, **contacts,
        }
        if rs_title:
            row["rs_research_title"] = rs_title
        if det.posted:
            row["posted_at"] = datetime(det.posted.year, det.posted.month, det.posted.day, tzinfo=_BELGRADE)
        deg = rs_text.degree_required(ad.conditions)
        if deg:
            row["degree_required"] = deg
        ct = rs_text.contract_type(ad.body)
        if ct:
            row["contract"] = ct["v"]
        dur = rs_text.duration_months(ad.body)
        if dur:
            row.update(duration_months_v=dur["v"], duration_months_conf=dur["conf"], duration_months_raw=dur["raw"])
        dd = rs_text.deadline_days(ad.conditions)
        if dd and det.posted:
            end = datetime(det.posted.year, det.posted.month, det.posted.day, 23, 59, 59, tzinfo=_BELGRADE) + timedelta(days=dd["days"])
            row["deadline"] = end.astimezone(timezone.utc)
            row["status"] = "expired" if end <= now else "open"
        rows.append(row)
    return rows


class NszConnector(Connector):
    source_id = "nsz"
    kind = "scrape_html"
    manifest = MANIFEST

    def ensure_source(self, conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('nsz','scrape_html',%s,%s,true,1.0,'RS',
                                'NSZ legal publication board; sections + employer detail pages (HTML); PDF archive = separate source nsz_pdf')
                        ON CONFLICT (id) DO NOTHING""", (BASE, BASE + SECTIONS["poslovi"]))

    def crawl_section(self, section: str, max_pages: int = 1, max_details: Optional[int] = None) -> Iterable[Dict[str, Any]]:
        """Yield listing rows for a section: list page(s) -> employer pages. Every page is stored raw with its envelope first."""
        base = BASE + SECTIONS[section]
        fetched = 0
        for page in range(1, max_pages + 1):
            url = base if page == 1 else f"{base}?page={page}"
            res, _ = self.fetch_raw(url)
            if res.status != 200:
                break
            for card in parse_listing(decode_bytes(res.content, res.content_type), url):
                if max_details is not None and fetched >= max_details:
                    return
                fetched += 1
                # F7-class fix (PM live audit 2026-09-24): one employer detail page failing after retries
                # must never abort the rest of this section's cards.
                try:
                    dres, dref = self.fetch_raw(card.url)
                except (FetchFailed, ManifestViolation, RobotsDenied):
                    continue
                if dres.status != 200:
                    continue
                detail_html = decode_bytes(dres.content, dres.content_type)
                yield from rows_from_detail(card, parse_detail(detail_html), dref.key, self.source_id, html=detail_html)

    PDF_MAX_BYTES = 25 * 1024 * 1024        # the largest observed issue is ~8 MB
    PDF_SPACING_S = 2.0                     # packet 004: backfill at <= 1 PDF / 2 s

    def list_pdf_issues(self):
        """Issues exactly as linked from the archive index (never guessed from numbers: names vary and numbers reset yearly)."""
        from ajp.connectors import nsz_pdf
        res, _ = self.fetch_raw(PDF_INDEX)
        return nsz_pdf.list_issues(decode_bytes(res.content, res.content_type)) if res.status == 200 else []

    def crawl_pdf_issue(self, issue=None, last_page: Optional[int] = None):
        """One weekly PDF issue (default: the newest listed) -> (rows, stats); index page and PDF are stored raw with envelopes."""
        from ajp.connectors import nsz_pdf
        if issue is None:
            issues = self.list_pdf_issues()
            if not issues:
                return [], {"error": "no issues found on index page"}
            issue = issues[0]
        res, ref = self.fetch_raw(issue.url, kind="pdf", max_bytes=self.PDF_MAX_BYTES)
        if res.status != 200:
            return [], {"error": f"HTTP {res.status}", "issue": issue.label}
        if "pdf" not in (res.content_type or "").lower():
            return [], {"error": f"unexpected content-type {res.content_type!r}", "issue": issue.label}
        ads = nsz_pdf.segment(list(nsz_pdf.iter_lines(res.content, last_page=last_page)))
        rows, stats = nsz_pdf.rows_from_ads(ads, issue, ref.key, self.source_id)
        stats.update(issue=issue.label, issue_date=issue.date.isoformat(), pdf_bytes=len(res.content))
        return rows, stats

    def crawl_pdf_backfill(self, years=None, max_issues: Optional[int] = None):
        """Yield (issue, rows, stats) for the listed issues of the given years, newest first. Default: the current year only;
        older years are an explicit opt-in. Failures are reported per issue, never raised past the loop."""
        from datetime import date as _date
        years = set(years) if years else {_date.today().year}
        done = 0
        for issue in [i for i in self.list_pdf_issues() if i.date.year in years]:
            if max_issues is not None and done >= max_issues:
                return
            if done:
                self.client._sleep(self.PDF_SPACING_S)
            done += 1
            try:
                rows, stats = self.crawl_pdf_issue(issue)
            except Exception as e:  # noqa: BLE001
                rows, stats = [], {"error": f"{type(e).__name__}: {str(e)[:120]}", "issue": issue.label}
            yield issue, rows, stats


class NszScheduledConnector(NszConnector):
    """F8 (PM live audit 2026-09-24): nsz.py's own multi-method API (crawl_section/crawl_pdf_issue/
    crawl_pdf_backfill) has no discover()+crawl(items) or bare crawl() shape ajp.scheduler's generic factory
    can call directly - this wraps it into exactly that bare crawl() shape (no arguments, the same shape
    ats_lever/ats_greenhouse/sljaka already use, per ajp.scheduler._discover_and_crawl's own docstring), for
    a routine SCHEDULED run: every configured HTML section (a bounded page/detail count - the routine
    day-to-day catch of newly posted ads, never a full re-crawl) plus the single newest PDF issue (this
    week's issue only - the full multi-year PDF backfill stays a deliberate, explicit one-off run via
    tools/, never something a daily cadence silently re-runs). A section or the PDF issue failing is recorded
    and skipped, never propagated - one bad section must never abort the others (same isolation discipline
    as every other connector's own per-item fault tolerance, F7)."""

    HTML_MAX_PAGES = 1
    HTML_MAX_DETAILS_PER_SECTION = 50

    def crawl(self) -> "tuple[List[Dict[str, Any]], Dict[str, Any]]":
        rows: List[Dict[str, Any]] = []
        report: Dict[str, Any] = {"sections": {}, "pdf_issue": None, "attempted": 0, "kept": 0, "discarded": 0}
        for section in SECTIONS:
            try:
                section_rows = list(self.crawl_section(section, max_pages=self.HTML_MAX_PAGES,
                                                       max_details=self.HTML_MAX_DETAILS_PER_SECTION))
            except (FetchFailed, ManifestViolation, RobotsDenied) as e:
                report["sections"][section] = {"error": f"{type(e).__name__}: {e}"}
                continue
            report["sections"][section] = {"rows": len(section_rows)}
            rows.extend(section_rows)
        try:
            pdf_rows, pdf_stats = self.crawl_pdf_issue()
            report["pdf_issue"] = pdf_stats
            if "error" not in pdf_stats:
                rows.extend(pdf_rows)
        except (FetchFailed, ManifestViolation, RobotsDenied) as e:
            report["pdf_issue"] = {"error": f"{type(e).__name__}: {e}"}
        report["attempted"] = len(rows)
        for r in rows:
            if r.get("kind") == "employment":
                report["kept"] += 1
            else:
                report["discarded"] += 1
        # If every single section AND the PDF issue failed outright, this is a real outage, not a
        # legitimately quiet week - raise so the scheduler's own R1 3-strikes counter and ledger
        # status='error'/'blocked' still see it (never silently reported as a clean, empty 'ok' run).
        section_errors = sum(1 for s in report["sections"].values() if "error" in s)
        if section_errors == len(SECTIONS) and (report["pdf_issue"] or {}).get("error"):
            raise FetchFailed("every nsz section and the PDF issue fetch failed")
        return rows, report
