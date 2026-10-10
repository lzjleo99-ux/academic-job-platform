"""NSZ weekly PDF publication 'Poslovi' -> listing rows (T6 PDF slice).

The publication is 3 text columns per page. Reading order is column 1, 2, 3 of page n, then page n+1, so an ad
that continues across columns/pages stays contiguous. Lines are classified by weight and size, not font names:
    employer header  bold, >= 10.3 pt      position title  bold, 8.5-10.3 pt      body  everything else >= 8 pt
Rotated margin text and the 7 pt footer are dropped. Only ads with an academic employer or position are turned
into rows (others are counted, not stored); the PDF itself is always kept raw.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pdfplumber
from selectolax.parser import HTMLParser

from ajp.connectors.jobposting import classify_location
from ajp.extract import rs_text
from ajp.extract.text import is_oversized_or_listlike_title
from ajp.extract_contacts import extract_contacts

_BELGRADE = ZoneInfo("Europe/Belgrade")
EMPLOYER, TITLE, BODY = "employer", "title", "body"
_ISSUE = re.compile(r"Број\s+([\d\-–]+)\s*-\s*(\d{1,2}\.\d{1,2}\.\d{4})")
_PDF_HREF = re.compile(r"/filemanager/Files/PDF_Arhiva_poslova/(\d{4})/[^?#\s]+\.pdf$")
MIN_FONT, HEADER_H, FOOTER_H = 8.0, 34.0, 28.0


@dataclass
class Issue:
    label: str
    date: date
    url: str


def list_issues(index_html: str, base: str = "https://www.nsz.gov.rs") -> List[Issue]:
    out = []
    for a in HTMLParser(index_html).css("a"):
        href = a.attributes.get("href") or ""
        m = _ISSUE.search(re.sub(r"\s+", " ", a.text()))
        if m and _PDF_HREF.search(href):
            d, mo, y = (int(x) for x in m.group(2).split("."))
            out.append(Issue(m.group(1), date(y, mo, d), href if href.startswith("http") else base + href))
    return sorted(out, key=lambda i: i.date, reverse=True)


@dataclass
class Line:
    page: int
    col: int
    top: float
    cls: str
    text: str
    section: str


def _cls(chars: List[Dict[str, Any]]) -> str:
    bold = sum("bold" in c["fontname"].lower() for c in chars) > len(chars) / 2
    size = sorted(c["size"] for c in chars)[len(chars) // 2]
    if bold and size >= 10.3:
        return EMPLOYER
    return TITLE if bold and size >= 8.5 else BODY


def iter_lines(pdf_bytes: bytes, first_page: int = 3, last_page: Optional[int] = None) -> Iterator[Line]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pno, pg in enumerate(pdf.pages, 1):
            if pno < first_page or (last_page and pno > last_page):
                continue
            w, h = pg.width, pg.height
            section = re.sub(r"\s+", " ", (pg.crop((0, 0, w, HEADER_H)).extract_text() or "")).strip()
            body = pg.filter(lambda o: o.get("object_type") != "char" or (
                o.get("upright", True) and o["size"] >= MIN_FONT and HEADER_H <= o["top"] <= h - FOOTER_H))
            for c in range(3):
                col = body.crop((w * c / 3, 0, w * (c + 1) / 3, h))
                for ln in col.extract_text_lines(return_chars=True):
                    txt = ln["text"].strip()
                    if txt:
                        yield Line(pno, c + 1, ln["top"], _cls(ln["chars"]), txt, section)


@dataclass
class PdfAd:
    employer: str
    address: str
    title: str
    body: str
    page_first: int
    page_last: int
    section: str


def _join(lines: List[str]) -> str:
    """Join wrapped lines; a trailing soft hyphen (U+00AD) means the word continues on the next line."""
    out = ""
    for t in lines:
        t = t.strip()
        if out.endswith("­"):
            out = out[:-1] + t
        else:
            out = f"{out} {t}" if out else t
    return re.sub(r"\s+", " ", out.replace("­", "")).strip()


def segment(lines: List[Line]) -> List[PdfAd]:
    ads: List[PdfAd] = []
    emp: List[str] = []
    addr: List[str] = []
    title: List[str] = []
    body: List[str] = []
    pages = [0, 0]
    section, prev = "", None

    def close():
        nonlocal title, body
        if title and (body or True):
            ads.append(PdfAd(_join(emp), _join(addr), _join(title), _join(body), pages[0], pages[1], section))
        title, body = [], []

    for ln in lines:
        if ln.section != section:
            close(); emp, addr = [], []
            section = ln.section
        if ln.cls == EMPLOYER:
            if prev != EMPLOYER:
                close(); emp, addr = [], []
            emp.append(ln.text)
        elif ln.cls == TITLE:
            if prev != TITLE:
                close(); pages[0] = ln.page
            title.append(ln.text)
        else:
            (body if title else addr).append(ln.text)
        if title:
            pages[1] = ln.page
        prev = ln.cls
    close()
    return [a for a in ads if a.employer]      # text before the first employer header is a continuation, not an ad


# T35b (packet 021, PM-verified defect, a real live issue-1216 row): 'Предавач' (Lecturer, a standard rank at
# vocational/professional academies - distinct from a university's own Доцент/Ванредни/Редовни професор) was
# missing entirely from this list, so a real block numbering several such positions ('3. Предавач за...
# 4. Предавач за... 5. Предавач за...') never split at all - the whole numbered run became ONE oversized
# title. Added, plus its Latin-script counterpart for consistency with every other rank word here.
_RANKS = ("Асистент|Доцент|Ванредни|Редовни|Редован|Професор|Наставник|Сарадник|Истраживач|Лектор|Научни|Виши|"
          "Предавач|"
          "Asistent|Docent|Vanredni|Redovni|Redovan|Profesor|Nastavnik|Saradnik|Istraživač|Istrazivac|Lektor|"
          "Naučni|Viši|Predavač")
# A real numbered list ('3. Предавач ...', '4. Предавач ...') puts a bare ordinal marker directly in front of
# the rank word - the lookahead now also recognises that shape, with a negative lookbehind excluding the
# split point BETWEEN the ordinal and the rank word itself (found live: '3.' otherwise also satisfies the
# main lookbehind's own '.' character, producing a spurious extra split that left '3.' as its own tiny
# fragment, decoupled from the real position title that follows it).
_NEW_POSITION = re.compile(rf"(?<=[\w.,)])(?<!\d[.)])\s+(?=(?:\d{{1,2}}[.)]\s*)?(?:{_RANKS})\b)")


def split_titles(title: str) -> List[str]:
    """One ad block often lists several positions in a row before one shared 'USLOVI' text. In running Serbian
    text a rank word is lower-case, so a capitalised rank word after other text starts a new position - a
    leading ordinal marker ('3. Предавач ...') is also recognised as starting a new position, kept attached
    to it rather than split off as its own fragment."""
    parts = [p.strip() for p in _NEW_POSITION.split(title) if p.strip()]
    return parts or [title]


def city_from(address: str) -> Optional[str]:
    m = re.search(r"\b\d{5}\s+([^,;]+)", address)
    return m.group(1).strip() if m else None


def rows_from_ads(ads: List[PdfAd], issue: Issue, raw_ref: str, source_id: str = "nsz",
                  now: Optional[datetime] = None) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Turn segmented blocks into listing rows: one row per academic position, shared conditions text per block."""
    now = now or datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    stats = {"ads_segmented": len(ads), "positions": 0, "academic": 0, "excluded_non_academic": 0}
    for block in ads:
        titles = split_titles(block.title)
        stats["positions"] += len(titles)
        for title in titles:
            if not rs_text.academic_ad(title, block.employer):
                stats["excluded_non_academic"] += 1
                continue
            stats["academic"] += 1
            row = _row(block, title, issue, raw_ref, source_id, now)
            if row["ext_uid"] not in seen:
                seen.add(row["ext_uid"])
                rows.append(row)
    return rows, stats


def _row(block: PdfAd, title: str, issue: Issue, raw_ref: str, source_id: str, now: datetime) -> Dict[str, Any]:
    full = f"{block.employer} {block.address} {title} {block.body}"
    uid = f"{issue.date.year}-{issue.label}-" + hashlib.sha1(f"{block.employer}|{title}|{block.body[:200]}".encode()).hexdigest()[:10]
    kind = rs_text.classify_kind(f"{title} {block.employer}")
    pk, rs_title = rs_text.position_kind(title)
    location = classify_location(full, has_country=True)   # T23 (PM ruling R2) completeness fix
    contacts = extract_contacts(text=full)                 # a PDF's own extracted text, no mailto:/tel: markup
    page_url = f"{issue.url}#page={block.page_first}"
    # T35b (packet 021) defensive backstop: split_titles' own rank-word/ordinal fix above should already
    # prevent an oversized multi-position title in practice - kept here in case some other real block shape
    # still evades it, so a row is never stored with a raw multi-position wall as its own title. kind/
    # position_kind are still classified from the FULL original title (more context, same as before) - only
    # the STORED title_raw ever falls back.
    stored_title = title
    if is_oversized_or_listlike_title(stored_title):
        first_sentence = re.split(r"[.!?]\s|\n", stored_title, maxsplit=1)[0].strip()
        stored_title = (first_sentence if first_sentence and not is_oversized_or_listlike_title(first_sentence)
                       else f"Position(s) announced by {block.employer}" if block.employer else "Untitled position notice")
    row: Dict[str, Any] = {
        "ext_uid": uid, "source_id": source_id, "url": page_url, "title_raw": stored_title,
        "org_raw": block.employer, "country_iso2": "RS", "city": city_from(block.address), "position_kind": pk,
        "kind": "employment" if kind == "other" else kind,          # an entry of the job publication is a job ad
        "ad_lang": rs_text.script_of(full), "raw_text": full, "requirements_raw": block.body or None, "raw_html_ref": raw_ref,
        "posted_at": datetime(issue.date.year, issue.date.month, issue.date.day, tzinfo=_BELGRADE),
        "foreigner_eligible": "unknown", "extraction_family": "nsz_pdf",
        "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
        "location_class_raw": location["location_class_raw"],
        "apply_url": page_url, **contacts,
    }
    if rs_title:
        row["rs_research_title"] = rs_title
    if (deg := rs_text.degree_required(block.body)):
        row["degree_required"] = deg
    if (ct := rs_text.contract_type(f"{title} {block.body[:300]}")):
        row["contract"] = ct["v"]
    if (dur := rs_text.duration_months(f"{title} {block.body[:300]}")):
        row.update(duration_months_v=dur["v"], duration_months_conf=dur["conf"], duration_months_raw=dur["raw"])
    if (dd := rs_text.deadline_days(block.body)):
        end = datetime(issue.date.year, issue.date.month, issue.date.day, 23, 59, 59, tzinfo=_BELGRADE) + timedelta(days=dd["days"])
        row["deadline"] = end.astimezone(timezone.utc)
        row["status"] = "expired" if end <= now else "open"
    return row
