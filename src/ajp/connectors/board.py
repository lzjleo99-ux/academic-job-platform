"""Generic organisation-board connector (T5; reused by T8-T11): a page of dated announcements -> listing rows.

Item extraction tries three structural families, first hit wins:
  1. <article> cards (custom CMS 'vest' cards, WordPress themes): title = first heading link, date = <time datetime> / text
  2. WordPress permalinks  /YYYY/MM/DD/slug/  (duplicates such as 'read more' anchors collapse onto the longest title)
  3. fallback: anchors in the content region whose text is classifiable
Site chrome (nav/header/footer/aside/menus/widgets) is never a source of items. An item becomes a row only if its title
is classifiable (kind != other) or names an academic position, so news posts are dropped.

Row fields come from the listing item alone (title, board-stated date). ``BoardConnector.crawl`` then makes at most
one further request per kept employment row - the row's own URL - to look for the explicit Serbian legal-deadline
sentence ("rok ... N dana od dana objavljivanja/oglasavanja", the same rule ``rs_text.deadline_days`` already applies
to NSZ ads); everything else (degree, salary, requirements) still stays NULL unless the listing title itself states it
(D6). Detail fetches go through the same manifest and robots gate as everything else, so the detail host must be in
the organisation's own family; a denial there ends that row's enrichment, never the crawl.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser, Node

from ajp import orgs as orgs_mod
from ajp.connectors.base import Connector, FetchFailed, FetchResult, ManifestViolation, UrlManifest, is_transport_error
from ajp.crawl_state import CrawlState
from ajp.extract import rs_text
from ajp.extract.document import extract_document_text
from ajp.extract.text import decode_bytes as _decode_html
from ajp.extract.text import decode_entities, is_oversized_or_listlike_title
from ajp.extract_contacts import extract_contacts
from ajp.ingest import upsert_listing_deduped
from ajp.rawstore import put_raw
from ajp.robots import RobotsDenied

MAX_PAGES_HARD = 10        # absolute ceiling whatever an org's crawl_config says

# T34 (packet 018/020, PM-verified defect, 33 real rows) / T35a (packet 021, centralized): the real root
# cause of the 33 mojibake title_raw/raw_text rows PM found - never the PDF-extraction path at all
# (content_scanned was already correctly True on every affected row) - is that every HTML fetch in this
# module hardcoded utf-8 decoding regardless of what the server itself actually declared. Found live tracing
# PM's own named example row (medf.kg.ac.rs/.../Konkursi_od_03062026_POSLOVI.pdf): its real listing page's
# own stored raw bytes decode to readable Cyrillic ('Објављени текст конкурса...') under windows-1251 - the
# SAME charset its own Content-Type header ('text/html; charset=windows-1251') already states - but utf-8
# decoding those same bytes with errors='replace' produces a stream of U+FFFD, which is exactly what ended
# up in title_raw (the anchor's own visible text) and the readable-suffix-plus-garbage-prefix shape in
# raw_text (teaser = anchor + filename_title(url), the filename half unaffected since it comes from the URL
# string, never HTML bytes). T35a (packet 021) moved this from a board.py-local fix to the ONE shared
# ajp.extract.text.decode_bytes helper (imported above as `_decode_html`, this module's own established
# name for it), since the identical hardcoded-utf-8 pattern existed at ~20 more connector call sites.

# T34 (packet 018/020): a defensive backstop on top of the charset fix above, for any OTHER encoding edge
# case this project has not hit yet - a title/teaser candidate is never stored if it is visibly garbled
# (U+FFFD from a bad decode, or stray control bytes), regardless of why. Falls back to the URL's own
# humanized filename (immune to the source page's own encoding, since it comes from the URL string, never
# the HTML bytes) and, failing even that, an honest generic placeholder - never silent garbage.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _has_garbled_text(text: str) -> bool:
    return "�" in text or bool(_CONTROL_CHARS.search(text))


# T35b (packet 021, PM-verified defect, 24 real open rows): a real anchor's own visible text can be the
# ENTIRE aggregated board announcement (medf.kg.ac.rs's own real example: a ~10k-char Cyrillic wall covering
# many separate calls, not one call's own title) rather than a single call's own short title - never usable
# as a title regardless of whether any individual character in it is garbled. Shared with nsz.py (see
# ajp.extract.text's own docstring - the same "one connector had the fix, others needed it too" pattern as
# T35a's charset centralization).
_title_is_oversized_or_listlike = is_oversized_or_listlike_title

_BELGRADE = ZoneInfo("Europe/Belgrade")            # legal deadlines run to end-of-day Belgrade time (same convention as nsz.py)
_CHROME_TAGS = {"nav", "aside", "footer"}          # header is handled separately: post titles often sit in <header class="entry-header">
# Exact chrome names, plus prefixed families. 'sidebar-right/left/layout' are PAGE LAYOUT classes on content wrappers
# (a page with a sidebar still has its posts in the content column), so 'sidebar-' only counts for widget-area suffixes.
_CHROME_TOKEN = re.compile(
    r"^(?:menu|nav|navbar|navigation|sidebar|footer|breadcrumbs?|pagination|social|cookie|widgets?)$"
    r"|^(?:widgets?|menu|nav|navbar)[-_].+$"
    r"|^sidebar[-_](?:primary|secondary|main|widgets?|area|inner|wrap|\d+)$"
    r"|^site[-_](?:header|footer|nav|navigation)\b.*$"
    r"|^(?:jf[-_])?mm[-_]pro.*$|^mega-?menu.*$", re.I)  # Joomla mega-menu plugins (e.g. 'jf_mm_pro*'): 'mm' doesn't
                                                         # contain the substring 'menu', so the rules above miss it
_READ_MORE = re.compile(
    # 'pogledaj(te)/погледај(те)' ('see/view [the call]') is the same class of call-to-action label as 'read more' -
    # gf.uns.ac.rs wraps its whole document link in nothing but '[Pogledajte konkurs]', a leading '[' tolerated by
    # the \W* prefix below.
    r"^\W*(pro[cč]itaj\w*|прочитај\w*|read more|saznaj\w*|сазнај\w*|pogledaj\w*|погледај\w*|više|више|detaljnije|детаљније|vi[sš]e)\b"
    r"|^\W*\.{2,}", re.I)
_WP_PERMALINK = re.compile(r"/(\d{4})/(\d{2})/(?:(\d{2})/)?[^/]+/?$")
_DMY = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})")
_HR_HOST = re.compile(r"^(zaposleni|servisi|login|sso|mail)\.")

# Serbian month names, nominative + genitive + abbreviation, Latin only - matched against normalize_title()'s
# output, which already transliterates Cyrillic to this same Latin spelling (see ajp.dedupe_keys), so no
# separate Cyrillic table is needed.
_MONTHS_LATIN = {
    "januar": 1, "januara": 1, "jan": 1, "februar": 2, "februara": 2, "feb": 2,
    "mart": 3, "marta": 3, "mar": 3, "april": 4, "aprila": 4, "apr": 4,
    "maj": 5, "maja": 5, "jun": 6, "juna": 6, "jul": 7, "jula": 7,
    "avgust": 8, "avgusta": 8, "avg": 8, "septembar": 9, "septembra": 9, "sep": 9,
    "oktobar": 10, "oktobra": 10, "okt": 10, "novembar": 11, "novembra": 11, "nov": 11,
    "decembar": 12, "decembra": 12, "dec": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS_LATIN, key=len, reverse=True))  # longest-first: 'septembra' before 'sep'
_MONTH_NAME_DATE = re.compile(rf"\b(\d{{1,2}})\.?\s+({_MONTH_ALT})\.?\s+(\d{{4}})\.?\b")
_LONE_MONTH_NAME_DATE = re.compile(rf"^\s*(\d{{1,2}})\.?\s+({_MONTH_ALT})\.?\s+(\d{{4}})\.?\s*$")


def _month_date(match: "re.Match[str]") -> Optional[date]:
    day, month, year = match.group(1), match.group(2), match.group(3)
    d = _mk(int(year), _MONTHS_LATIN[month], int(day))
    return d if d and FILENAME_YEAR_MIN <= d.year <= datetime.now(timezone.utc).year + 1 else None


def parse_month_name_date(text: str) -> Optional[date]:
    """'21 Avgust 2026'/'21. avgusta 2026.'/'21 авг 2026' (either script, full name or abbreviation, nominative
    or genitive). Same year bound and invalid-calendar-date guard as every other date parser here."""
    from ajp.dedupe_keys import normalize_title
    m = _MONTH_NAME_DATE.search(normalize_title(text))
    return _month_date(m) if m else None


class OrgManifest:
    """Manifest for an organisation's own host family: {host, www.host, www1.host ...}, never other subdomains and never HR/login hosts."""

    @staticmethod
    def for_hosts(hosts: Iterable[str]) -> UrlManifest:
        cores = set()
        for h in hosts:
            if not h:
                continue
            if _HR_HOST.match(h):
                raise ValueError(f"HR / login host is out of scope (R4): {h}")
            cores.add(re.sub(r"^(www\d?|en)\.", "", h.lower()))
        if not cores:
            raise ValueError("no hosts")
        alt = "|".join(re.escape(c) for c in sorted(cores))
        return UrlManifest(rf"https?://(?:www\d?\.|en\.)?(?:{alt})(?::\d+)?(?:[/?#].*)?")


@dataclass
class Item:
    url: str
    title: str
    posted: Optional[date]
    teaser: str
    family: str
    context: str = ""          # URL of the board page the item was found on (used as a kind hint for document links)
    date_basis: Optional[str] = None
    date_conf: float = 0.0
    date_raw: Optional[str] = None          # URL of the board page the item was found on (used as a kind hint for document links)


# --- staleness / posted-date policy (PM q1/q2 approvals, 2026-09-22) --------------------------------------------
# DISPLAY policy only: neither branch below ever deletes or hides a row.
#   * a row with a stated posted_at older than STALE_AFTER_DAYS (config: crawl_config.stale_after_days, else this
#     module default) is stored status='expired' when no explicit deadline exists to say otherwise;
#   * a row with NO posted_at at all is stored status='unknown' (not 'open': its age truly cannot be judged) and
#     stays that way forever unless a deadline is later found - a date is never invented to move it out of 'unknown'.
# A client's default view is status='open'; 'expired'/'unknown' remain in the database and are reachable via a
# status filter (T14). Overridable per organisation via crawl_config.stale_after_days for boards with unusual
# archive-vs-current mixes (e.g. an org whose 'current' postings are themselves months old).
STALE_AFTER_DAYS = 120
FILENAME_YEAR_MIN = 2010   # q1: a filename year outside [2010, this_year+1] is not trusted as a real posting date
_FN_DATES = (re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)"),                       # 16092026
             re.compile(r"(?<!\d)(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)"),            # 2026-09-16
             re.compile(r"(?<!\d)(\d{1,2})[-_.](\d{1,2})[-_.](20\d{2})(?!\d)"))            # 16.09.2026
_DOC_EXT = re.compile(r"\.(?:pdf|docx?|odt|xlsx?|rtf)$", re.I)
_DOC_EMPLOYMENT_WORDS = re.compile(r"\b(konkurs\w*|oglas\w*|izbor\w*|zvanj\w*)\b")
_SLUG_ID = re.compile(r"\d{3,}")


def _is_document_url(url: str) -> bool:
    return bool(_DOC_EXT.search(urlsplit(url).path))


def post_like_url(url: str) -> bool:
    """A URL that points at ONE thing (a post, a document, a detail page) rather than at a section or a menu entry:
    a WordPress permalink, an uploaded document, a document extension, a numeric id, or a long descriptive slug."""
    path = urlsplit(url).path
    last = path.rstrip("/").rsplit("/", 1)[-1]
    return bool(_WP_PERMALINK.search(path) or "/wp-content/uploads/" in path or _DOC_EXT.search(path)
                or _SLUG_ID.search(last) or last.count("-") >= 3)


def date_from_filename(url: str) -> Optional[date]:
    """A full, valid date written in the FILE NAME (not the directory: 'uploads/2019/03/' is only a month) matching
    one of the three strict forms yyyy-mm-dd / ddmmyyyy / dd.mm.yyyy, with a plausible year (q1 approval). Anything
    else - no date, an ambiguous fragment, an implausible year - is None; never guessed."""
    from urllib.parse import unquote
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    year_max = datetime.now(timezone.utc).year + 1
    for i, rx in enumerate(_FN_DATES):
        m = rx.search(name)
        if not m:
            continue
        a, b, c = (int(x) for x in m.groups())
        d = _mk(c, b, a) if i in (0, 2) else _mk(a, b, c)
        if d and FILENAME_YEAR_MIN <= d.year <= year_max:
            return d
    return None


def document_posted_date(anchor_text: str, url: str) -> Tuple[Optional[date], Optional[str], float, Optional[str]]:
    """(date, basis, confidence, raw excerpt) for a document-family item (q1 approval). Only ``date_from_filename``
    is trusted; if the visible anchor text ALSO states a date and it disagrees, the result is unknown rather than a
    guess at which one is right. basis is always 'filename' when a date is returned - the only source this uses."""
    fn = date_from_filename(url)
    if fn is None:
        return None, None, 0.0, None
    m = _DMY.search(anchor_text or "")
    if m:
        try:
            anchor_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            anchor_date = None
        if anchor_date and anchor_date != fn:
            return None, None, 0.0, None
    from urllib.parse import unquote
    return fn, "filename", 0.4, unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def filename_title(url: str) -> str:
    """'Konkurs-EF12_docent.docx' -> 'Konkurs EF12 docent' (percent-decoding handled)."""
    from urllib.parse import unquote
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    return _clean(re.sub(r"[-_+]+", " ", _DOC_EXT.sub("", name)))

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


def in_chrome(node: Node) -> bool:
    """True if the node sits in site chrome (navigation, sidebar, footer, menu/widget blocks).
    Layout classes on <html>/<body> ('has-sidebar') say nothing about the node, and a <header> inside an <article>/<main>
    is the post's own header, not the site header."""
    ancestors, p = [], node
    while p is not None and p.tag not in ("html", "body", "-undef"):
        ancestors.append(p)
        p = p.parent
    in_content = any(x.tag in ("article", "main") for x in ancestors)
    for x in ancestors:
        if x.tag in _CHROME_TAGS or (x.tag == "header" and not in_content):
            return True
        tokens = (x.attributes.get("class") or "").split() + (x.attributes.get("id") or "").split()
        if any(_CHROME_TOKEN.match(t) for t in tokens):
            return True
    return False


def _date_from(text: str = "", dt_attr: str = "", href: str = "", title: str = "") -> Optional[date]:
    """Publication date, most authoritative first: <time datetime>, a full /YYYY/MM/DD/ permalink, then visible text.
    The title is removed before the text search: 'Konkurs za izbor ... 10.06.2026.' names a session date, not the posting date."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", dt_attr or "")
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _WP_PERMALINK.search(urlsplit(href).path)
    if m and m.group(3):
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))      # month-only paths: no day guessed
    remainder = (text or "").replace(title, " ") if title else (text or "")
    m = _DMY.search(remainder)
    if m:
        return _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return parse_month_name_date(remainder)


def _mk(y: int, mo: int, d: int) -> Optional[date]:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


_ENDS_READ_MORE = re.compile(r"\b(vi[sš]e|more)\.?\s*$", re.I)


_DATE_SEQUENCE_TOKEN = re.compile(r"\b\d{1,2}\.\d{1,2}\.?\b")


def _is_date_sequence_title(t: str) -> bool:
    """T31 (packet 019): a monthly-archive index page's own anchor 'title' is sometimes just a bare list of
    dd.mm. tokens and nothing else (PM's own real example: '23.09. 16.09. 22.07. 03.06. ...') - never a real
    announcement title, however long the resulting joined string looks. Only rejected when almost NOTHING
    besides date tokens and glue punctuation/whitespace remains (>=3 tokens, so a single genuinely-dated
    title like 'Konkurs za izbor... 10.06.2026. godine' - which still has a real sentence around its one
    date - is never caught by this)."""
    tokens = _DATE_SEQUENCE_TOKEN.findall(t)
    if len(tokens) < 3:
        return False
    remaining = _DATE_SEQUENCE_TOKEN.sub("", t)
    return len(re.sub(r"[\s,;.\-–—]+", "", remaining)) == 0


def _good_title(t: str) -> bool:
    """A usable announcement title: long enough, not a 'read more' link, not a bare URL (an anchor whose text is its own address),
    not a bare date-sequence archive index. A label ending in 'više'/'more' (the idiomatic Serbian/English 'read more' tail) is
    only rejected on this basis when the whole string is short (<=30 chars) - a genuinely long sentence that happens to end the
    same way must not be swept in."""
    if len(t) < 12:
        return False
    if len(t) <= 30 and _ENDS_READ_MORE.search(t):
        return False
    if _is_date_sequence_title(t):
        return False
    return not _READ_MORE.search(t) and not re.match(r"^\s*(https?://|www\.)\S+\s*$", t)


def _looks_like_read_more(text: str) -> bool:
    """A short 'call to action' link label (Pročitaj više/Saznajte više/Read more/Preuzmi/...) rather than a
    real title - the signal the card family uses to find a candidate link, as distinct from just 'too short'."""
    return bool(_READ_MORE.search(text) or (len(text) <= 30 and _ENDS_READ_MORE.search(text))
                or _is_generic_label(text))


def _nearest_card_container(anchor_node: Node, max_levels: int = 6) -> Tuple[Optional[Node], Optional[Node]]:
    """Walk up from a generic 'read more' anchor; return (container, heading) at the FIRST ancestor level whose
    descendant headings (h2-h6, real text, no anchor of their own) number EXACTLY one - a single-item card.
    Bails out to (None, None) as soon as an ancestor holds more than one such heading (it spans multiple cards;
    never guess which one the read-more link belongs to), or after max_levels with none found. h1 is excluded
    for the same reason as _nearby_heading_title: a page has at most one, so it is the page's own title."""
    n, depth = anchor_node.parent, 0
    while n is not None and depth < max_levels:
        heads = [h for h in n.css("h2, h3, h4, h5, h6") if h.css_first("a[href]") is None and _good_title(_clean(h.text()))]
        if len(heads) == 1:
            return n, heads[0]
        if len(heads) > 1:
            return None, None
        n, depth = n.parent, depth + 1
    return None, None


_GENERIC_DOC_LABEL = re.compile(
    # 'prilog'/'prilozi' (attachment/attachments) is a g->z stem alternation, not a suffix - \w* cannot cover the
    # plural, so both forms are spelled out (same for documenti/documenta).
    r"^(текст конкурса|tekst konkursa|преузми\w*|preuzmi\w*|прило[гз]\w*|prilo[gz]\w*|линк\w*|link\w*|"
    r"документ\w*|dokument\w*|download|детаљније|detaljnije|више|vise|pdf|word|doc[x]?)"
    r"(\.|\s*\((?:pdf|word|docx?|doc)\))?$", re.I)  # tolerate a trailing '.' or a format annotation like ' (PDF)'
# elfak.ni.ac.rs: a PUBLICATION-LOCATION notice ('(Tekst) konkursa je objavljen u [Niškim] Narodnim novinama
# [dd.mm.yyyy. godine]') names where the ad appeared, not what it's for - the position is only in the filename
# ('saradnik-u-nastavi-konkurs-09-01-2026.pdf'). Unlike _GENERIC_DOC_LABEL's fixed short labels, the newspaper
# name/date trails and varies, so this is matched as a PREFIX only (no '$' anchor), not the whole string. Some
# live pages mix scripts within one sentence ('Конкурс je објављен...' - Cyrillic word, Latin 'je') so this is
# matched against normalize_title()'s Latin-only output rather than duplicating a Cyrillic/Latin pair like
# _GENERIC_DOC_LABEL does - one pattern covers both scripts and any mixing between them.
_PUBLICATION_NOTICE_LABEL = re.compile(r"^(tekst konkursa|konkurs\w*) (je )?objavljen\w* u\b", re.I)
# medf.kg.ac.rs: a VIEWING-INSTRUCTION sentence ('Objavljeni tekst konkursa u oglasnim novinama POSLOVI mozete
# pogledati', 'Kompletan tekst konkursa mozete pogledati.') names neither the position nor where it was published -
# it just tells the reader they CAN look at the text (elsewhere/below), in whatever word order. Unlike the two
# patterns above (which anchor a fixed opening), this one only requires a 'view/see' verb and a 'the text/the
# competition' noun to BOTH appear, gated by length (<=200 chars) so it never fires on a real long ad body that
# happens to use 'pogledati' once in passing.
_VIEWING_VERB = re.compile(r"\bpogleda\w*|\bvid(i|ite|eti)\w*|\bpregleda\w*", re.I)
_TEXT_OR_CALL_NOUN = re.compile(r"\btekst konkursa\b|\bkonkurs\w*\b", re.I)
_VIEWING_INSTRUCTION_MAX_LEN = 200


def _is_generic_label(text: str) -> bool:
    t = text.strip()
    if _GENERIC_DOC_LABEL.match(t):
        return True
    from ajp.dedupe_keys import normalize_title
    n = normalize_title(t)
    if _PUBLICATION_NOTICE_LABEL.match(n):
        return True
    return len(t) <= _VIEWING_INSTRUCTION_MAX_LEN and bool(_VIEWING_VERB.search(n)) and bool(_TEXT_OR_CALL_NOUN.search(n))


def _document_title(anchor_node: Node, anchor_text: str, url: str) -> str:
    """The visible anchor text, UNLESS it is a generic document-link label ('Текст конкурса', 'Преузми') - three
    patterns are seen live for where the real text then is:
      1. it is the REST of the anchor's own containing paragraph (agrif.bg.ac.rs: one <p> per call, ending in a
         generic '... Текст конкурса' link) - use the block's full text with the trailing label stripped;
      2. it sits in a SIBLING heading (an accordion '.toggle' link wrapping an <h1-6> next to the document link:
         sfb.bg.ac.rs) - see ``_nearby_heading_title``.
      3. it sits in a SIBLING TABLE CELL (np.ac.rs/DUNP: a bare 'PDF' download button in its own <td>, the real
         title one cell over) - see ``_table_row_title``.
    Falls back to the anchor text or the filename if none apply."""
    if _good_title(anchor_text) and not _is_generic_label(anchor_text):
        return anchor_text
    block_title = _own_block_title(anchor_node, anchor_text)
    if block_title:
        return block_title
    heading = _nearby_heading_title(anchor_node)
    if heading:
        return heading
    row_title = _table_row_title(anchor_node)
    if row_title:
        return row_title
    return filename_title(url)   # anchor_text was already established generic above; never return it here


_LONE_DATE = re.compile(r"^\s*(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\.?\s*$")   # the WHOLE sibling text is only a date


def visible_sibling_date(anchor_node: Node) -> Optional[Tuple[date, str]]:
    """A date stated as its OWN paragraph immediately before the item's containing block (agrif.bg.ac.rs: one
    ``<p>dd.mm.yyyy.</p>`` right before that item's own ``<p>title ... link</p>``, with no filename date to use).
    Single hop only, from the anchor's immediate parent, skipping only pure whitespace - this must never cross
    into a PREVIOUS item's own content, so nothing further back is tried. The sibling's ENTIRE text must be just
    the date (no title text alongside it); same strict shape and year bound as a filename date (q1 parity)."""
    block = anchor_node.parent
    if block is None:
        return None
    sib = block.prev
    while sib is not None and sib.tag == "-text" and not _clean(sib.text()):
        sib = sib.prev
    if sib is None:
        return None
    sib_text = _clean(sib.text())
    m = _LONE_DATE.match(sib_text)
    if m:
        day, mo, year = (int(x) for x in m.groups())
        if not (FILENAME_YEAR_MIN <= year <= datetime.now(timezone.utc).year + 1):
            return None
        d = _mk(year, mo, day)
        return (d, sib_text) if d else None
    from ajp.dedupe_keys import normalize_title
    m = _LONE_MONTH_NAME_DATE.match(normalize_title(sib_text))
    if m:
        d = _month_date(m)
        return (d, sib_text) if d else None
    return None


def _own_block_title(anchor_node: Node, anchor_text: str) -> Optional[str]:
    """The anchor's immediate containing block (its parent), when that block holds real text beyond the anchor's
    own generic label - i.e. the anchor sits at the end of a descriptive paragraph, not alone in its own wrapper."""
    block = anchor_node.parent
    if block is None:
        return None
    full = _clean(block.text())
    if len(full) < len(anchor_text) + 15:      # not meaningfully more than the anchor's own (generic) text
        return None
    candidate = _clean(full[: -len(anchor_text)]) if full.endswith(anchor_text) else full.replace(anchor_text, " ").strip()
    candidate = _clean(candidate)
    return candidate if _good_title(candidate) and not _is_generic_label(candidate) else None


def _table_row_title(anchor_node: Node) -> Optional[str]:
    """np.ac.rs (DUNP): a document link sits ALONE in its own <td> ('PDF' button, nothing else - _own_block_title
    finds no extra text there), but a SIBLING <td> in the same <tr> holds the real title ('Оглас за избор
    демонстратора'), often alongside a short type-badge cell ('ОГЛАС') that _good_title's length floor already
    excludes. Picks the first sibling cell with real, non-generic text; None outside a <tr>/<td> structure."""
    td = anchor_node
    while td is not None and td.tag != "td":
        td = td.parent
    if td is None or td.parent is None or td.parent.tag != "tr":
        return None
    for cell in td.parent.css("td"):
        if cell is td:
            continue
        text = _clean(cell.text())
        if _good_title(text) and not _is_generic_label(text):
            return text
    return None


_HEADING_TAGS = ("h2", "h3", "h4", "h5", "h6")     # h1 is excluded: a page has (at most) one, so it is the PAGE title,
                                                    # never a per-item one, even when it sits right before the first item


def _nearby_heading_title(node: Node, max_levels: int = 4) -> Optional[str]:
    """Walk up to ``max_levels`` ancestors of ``node``; at each level, look at the nearest preceding sibling that
    is not pure whitespace (skipping only blank text between tags) for a heading tag or an element wrapping one
    (e.g. an accordion '.toggle' link wrapping an <h3>). Stops at the first substantive, non-heading sibling
    without going further back, so a page-level heading shared by many unrelated items several siblings away is
    never picked up. Used only when the item's own anchor text is generic (see ``_document_title``)."""
    n, depth = node, 0
    while n is not None and depth < max_levels:
        sib = n.prev
        while sib is not None and sib.tag == "-text" and not _clean(sib.text()):
            sib = sib.prev            # skip only pure whitespace between tags
        if sib is not None:
            h = sib if sib.tag in _HEADING_TAGS else sib.css_first(", ".join(_HEADING_TAGS))
            if h is not None:
                t = _clean(h.text())
                if len(t) >= 20:
                    return t
        n, depth = n.parent, depth + 1
    return None


_SITEMAP_URL = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>(?:\s*<lastmod>\s*([^<\s]+)\s*</lastmod>)?", re.I)


def slug_title(url: str) -> str:
    """The last path segment of a URL, hyphens/underscores to spaces ('izbor-u-zvanje-nastavnika-2014' ->
    'izbor u zvanje nastavnika 2014') - the filename_title() convention, for a Drupal/CMS node URL with no file
    extension rather than an uploaded document."""
    from urllib.parse import unquote
    seg = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    return _clean(re.sub(r"[-_+]+", " ", seg))


def parse_sitemap_items(xml_text: str, *, include: Iterable[str] = (), exclude: Iterable[str] = ()) -> List[Item]:
    """va.mod.gov.rs: a flat Drupal sitemap.xml is the ONLY discovery route (node ids are never guessed) - each
    surviving <loc> becomes one item, undated (a sitemap <lastmod> reflects when the PAGE was last edited, not
    when the posting first went up, so it is never used as posted_at; the row stays status='unknown' rather than
    guess a first-posted date from it - see rows_from_items' staleness policy). include/exclude are lowercase
    substrings checked against the URL PATH: include selects candidate nodes at all (e.g. 'konkurs'), exclude
    drops a hard-excluded class (R5 'interni-konkurs', R7 'uvid-javnosti') before the item is even created - the
    Serbian classifier then makes the real kind decision on each survivor's slug-derived title, same as any
    other family; this is only a coarse pre-filter over ~1600+ unrelated URLs, not the classification itself."""
    items: List[Item] = []
    for m in _SITEMAP_URL.finditer(xml_text):
        loc = m.group(1)
        path = urlsplit(loc).path.lower()
        if include and not any(s in path for s in include):
            continue
        if exclude and any(s in path for s in exclude):
            continue
        title = slug_title(loc)
        if not _good_title(title):
            continue
        items.append(Item(loc, title, None, title, "sitemap"))
    return _dedupe(items)


def parse_items(html: str, page_url: str) -> List[Item]:
    tree = HTMLParser(html)
    items: List[Item] = []
    # Joomla's own category-blog view wraps each post in a plain <div>, never <article> - two class conventions
    # seen live for the SAME semantic structure (one heading+link per post): 'item' (+ a 'column-N' grid class,
    # filum.kg.ac.rs) and 'blog-item'/'com-content-category-blog__item' (fsfv.ni.ac.rs, a newer Joomla template).
    # Exact class-token match only (selectolax '.item' never matches a menu li's 'item-141'-style id-suffixed
    # token), and every check below (in_chrome, a real heading+link) applies identically - this is the article
    # family's own logic, just not gated on the tag name.
    for art in tree.css("article, div.item, div.blog-item, div.com-content-category-blog__item"):
        if in_chrome(art):
            continue
        a = next((x for h in art.css("h1 a, h2 a, h3 a, h4 a, h5 a") for x in [h] if _good_title(_clean(x.text()))), None) \
            or next((x for x in art.css("a[href]") if _good_title(_clean(x.text()))), None)
        doc_hrefs = {urljoin(page_url, x.attributes.get("href", "")) for x in art.css("a[href]")
                     if _is_document_url(urljoin(page_url, x.attributes.get("href", "")))}
        doc = next((x for x in art.css("a[href]") if _is_document_url(urljoin(page_url, x.attributes.get("href", "")))), None)
        if a is None and doc is None:
            continue
        # akademija.uns.ac.rs: a WordPress static PAGE's whole body is ALSO one top-level <article> (class
        # 'post-NNN page type-page') - not a one-item card. Its many unrelated document links must never collapse
        # onto just the first one found; skip the whole article here and let family 3 parse each one individually.
        if len(doc_hrefs) > 1:
            continue
        teaser = _clean(art.text(separator=" "))
        if doc is not None and (a is None or _is_document_url(urljoin(page_url, a.attributes.get("href", "")))):
            # an article that wraps an attachment IS a document item, whatever its link text says ('Преузми' = 'Download')
            href = urljoin(page_url, doc.attributes.get("href", ""))
            anchor = _clean(doc.text())
            dt, basis, conf, raw = document_posted_date(anchor, href)
            if dt is None and (vs := visible_sibling_date(doc)):
                dt, basis, conf, raw = vs[0], "visible_text", 0.4, vs[1]
            items.append(Item(href, _document_title(doc, anchor, href), dt,
                              _clean(f"{anchor} {filename_title(href)}"), "document", page_url, basis, conf, raw))
            continue
        t = art.css_first("time")
        items.append(Item(urljoin(page_url, a.attributes.get("href", "")), _clean(a.text()),
                          _date_from(teaser, (t.attributes.get("datetime") if t else "") or "", a.attributes.get("href", ""), _clean(a.text())),
                          teaser[:400], "article"))
    # filum.kg.ac.rs: Joomla's category-blog view often has a SECOND container - 'items-more' (an
    # ol/ul.nav.nav-tabs.nav-stacked "more articles" sidebar list, no excerpt) - holding ADDITIONAL posts absent
    # from the main column above (a staff-mobility grant_call was found live only here). Merged into the SAME
    # family-1 results (URL-deduped below), not a new cascade tier: family 1 still wins the page either way.
    seen_urls = {i.url for i in items}
    for a in tree.css("div.items-more a[href], .items-more a[href]"):
        # no in_chrome() check here: Joomla's 'more articles' list is typically an ol/ul with Bootstrap's own
        # 'nav nav-tabs nav-stacked' classes (a tab-list widget, not this project's site-navigation chrome) -
        # in_chrome's generic 'nav' token would wrongly exclude it. The '.items-more' selector is itself already
        # the deliberate scoping (PM's own resolution spec), so nothing further needs excluding here.
        text = _clean(a.text())
        href = urljoin(page_url, a.attributes.get("href", ""))
        if not _good_title(text) or href in seen_urls:
            continue
        seen_urls.add(href)
        items.append(Item(href, text, None, text, "article"))
    if items:
        return _dedupe(items)
    # family 1.5: a Joomla category LIST view (com_content 'table' layout: one <tr> per article, a title cell
    # class="list-title" and often a date cell class="list-date"). Selected via the title cell itself, not by
    # scanning every <table> on the page, so an unrelated table (e.g. a staff/schedule table) is never touched.
    for title_cell in tree.css("th.list-title, td.list-title"):
        row = title_cell.parent
        a = title_cell.css_first("a[href]")
        if row is None or a is None or in_chrome(row) or not _good_title(_clean(a.text())):
            continue
        date_cell = row.css_first("td.list-date, .list-date")
        posted = _date_from(_clean(date_cell.text())) if date_cell is not None else None
        items.append(Item(urljoin(page_url, a.attributes.get("href", "")), _clean(a.text()), posted,
                          _clean(row.text(separator=" "))[:400], "table"))
    if items:
        return _dedupe(items)
    # family 1.75: a CARD layout (YOOtheme '.el-item' and similar) where the title lives in a separate heading
    # (h2-h6, no anchor of its own) and the only navigable link is a generic 'read more'/'saznaj vise' button
    # elsewhere in the same card. Found via the read-more anchor -> its nearest ancestor holding EXACTLY one such
    # heading (never crossing into a sibling card: an ancestor with >1 heading means we climbed too far).
    seen_cards: set = set()
    card_items: List[Item] = []
    for a in tree.css("a[href]"):
        text = _clean(a.text())
        href = urljoin(page_url, a.attributes.get("href", ""))
        # a document (.pdf/.docx/...) with a generic label is the DOCUMENT family's job (own-block-text / nearby
        # heading, both already specific to that case); the card family is only for a 'read more' to a full page.
        if in_chrome(a) or not text or not _looks_like_read_more(text) or _is_document_url(href):
            continue
        container, heading = _nearest_card_container(a)
        if container is None or id(container) in seen_cards:
            continue
        seen_cards.add(id(container))
        heading_text = _clean(heading.text())
        meta_el = container.css_first(".el-meta, .uk-text-meta, time, .meta, .date")
        meta_text = _clean(meta_el.text()) if meta_el is not None else _clean(container.text(separator=" "))
        posted = _date_from(meta_text, title=heading_text)
        basis, conf, raw = ("visible_text", 0.4, meta_text) if posted else (None, 0.0, None)
        teaser = _clean(container.text(separator=" "))
        card_items.append(Item(urljoin(page_url, a.attributes.get("href", "")), heading_text, posted,
                               teaser[:400], "card", page_url, basis, conf, raw))
    # a genuine card LAYOUT repeats; a single isolated 'read more' link elsewhere on an otherwise-unrelated page
    # (polj.uns.ac.rs: one external conference link on a content-rich Drupal homepage) is chrome-shaped noise, not
    # a one-item board - trusting it would mask every real item family 3 would otherwise find on the same page
    # (the same lesson as the multi-document <article> fix above: require a repeating structure before believing it).
    if len(card_items) >= 2:
        items.extend(card_items)
        return _dedupe(items)
    best: Dict[str, Node] = {}
    for a in tree.css("a[href]"):
        href = urljoin(page_url, a.attributes.get("href", ""))
        # a document under /wp-content/uploads/YYYY/MM/... matches the same /YYYY/MM(/DD)?/ shape as a post permalink
        # but is an attachment, not a post - it belongs to the 'document' family (family 3), never here.
        if (_WP_PERMALINK.search(urlsplit(href).path) and not _is_document_url(href)
                and not in_chrome(a) and _good_title(_clean(a.text()))):
            if href not in best or len(_clean(a.text())) > len(_clean(best[href].text())):
                best[href] = a
    for href, a in best.items():
        holder = _holder(a, page_url) or a
        items.append(Item(href, _clean(a.text()), _date_from(_clean(holder.text()), "", href, _clean(a.text())), _clean(holder.text())[:400], "wp_permalink"))
    if items:
        return _dedupe(items)
    for a in tree.css("a[href]"):                       # family 3: document attachments (a document library page)
        href = urljoin(page_url, a.attributes.get("href", ""))
        if not _is_document_url(href) or in_chrome(a):
            continue
        anchor = _clean(a.text())
        dt, basis, conf, raw = document_posted_date(anchor, href)
        if dt is None and (vs := visible_sibling_date(a)):
            dt, basis, conf, raw = vs[0], "visible_text", 0.4, vs[1]
        items.append(Item(href, _document_title(a, anchor, href), dt, _clean(f"{anchor} {filename_title(href)}"), "document", page_url, basis, conf, raw))
    if items:
        return _dedupe(items)
    for a in tree.css("a[href]"):
        title = _clean(a.text())
        if in_chrome(a) or not _good_title(title) or (rs_text.classify_kind(title) == "other" and not rs_text.academic_ad(title)):
            continue
        holder = _holder(a, page_url) or a
        items.append(Item(urljoin(page_url, a.attributes.get("href", "")), title,
                          _date_from(_clean(holder.text()) if holder is not a else "", "", a.attributes.get("href", ""), title),
                          _clean(holder.text())[:400], "anchor"))
    return _dedupe(items)


_HOLDER_TAGS = {"li", "p", "tr", "td", "dd", "dt", "article"}


def _holder(a: Node, page_url: str) -> Optional[Node]:
    """The small element that holds exactly this item (li/p/tr/...), or None. A text date is only trusted from such an
    element: the parent of a bare link is often the container of ALL links and would lend one item another's date."""
    p, depth = a.parent, 0
    while p is not None and depth < 3 and p.tag not in ("body", "html"):
        if p.tag in _HOLDER_TAGS:
            hrefs = {urljoin(page_url, x.attributes.get("href", "")) for x in p.css("a[href]")}
            return p if len(hrefs) <= 1 else None
        p, depth = p.parent, depth + 1
    return None


def _dedupe(items: List[Item]) -> List[Item]:
    seen, out = set(), []
    for it in items:
        if it.url not in seen:
            seen.add(it.url)
            out.append(it)
    return out


def _keep_document(item: Item) -> Tuple[bool, str]:
    """A linked document is kept only if its anchor text or filename classifies. 'Konkurs-EF12.docx' alone is not an
    employment marker in general, so on a board whose own URL says it is an employment board (izbori u zvanja, konkursi...)
    'konkurs/oglas/izbor/zvanje' words count. Not parsed, not fetched: title + link only (low confidence, undated)."""
    text = f"{item.title} {filename_title(item.url)}"
    reason = rs_text.person_specific_procedure(text)
    if reason:
        return False, reason
    if rs_text.is_standing_procedure_document(text):
        return False, "other"          # a price list/rulebook/blank form template, not a call - never reached via the hint fallback either
    kind = rs_text.classify_kind(text)
    if kind != "other":
        return True, kind
    if rs_text.academic_ad(text):
        return True, "employment"
    hint = rs_text.classify_kind(re.sub(r"[-_/]+", " ", urlsplit(item.context).path))
    if hint == "employment" and _DOC_EMPLOYMENT_WORDS.search(rs_text.normalize_title(text)):
        return True, "employment"
    return False, "other"


def keep(item: Item) -> Tuple[bool, str]:
    """(True, kind) for a listing candidate; (False, reason) otherwise. reason is rs_text.EXCLUDED_PERSON_SPECIFIC for
    procedure-stage notices about a specific person (raw page stays in the raw store, but no row, name or URL is kept)."""
    if item.family == "document":
        return _keep_document(item)
    reason = rs_text.person_specific_procedure(item.title)
    if reason:
        return False, reason
    if item.family == "anchor" and not (item.posted or post_like_url(item.url)):
        return False, "other"                            # a bare section/menu link: neither dated nor post/document-like
    kind = rs_text.classify_kind(item.title)
    if kind != "other":
        return True, kind
    if rs_text.academic_ad(item.title):
        return True, "employment"
    return False, "other"


def rows_from_items(items: List[Item], org: Dict[str, Any], raw_ref: str, source_id: str = "rs_org_boards",
                    now: Optional[datetime] = None) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    now = now or datetime.now(timezone.utc)
    rows, stats = [], {"items": len(items), "dropped_not_classifiable": 0, "excluded_person_specific": 0}
    for it in items:
        ok, kind = keep(it)
        if not ok:
            stats["excluded_person_specific" if kind == rs_text.EXCLUDED_PERSON_SPECIFIC else "dropped_not_classifiable"] += 1
            continue
        # T29 (packet 017, PM-verified live): decoded ONCE here, the single place every Item (regardless of
        # which of this module's many discovery paths built it - HTML card, table row, document-anchor
        # fallback, sitemap slug...) becomes a row. A real selectolax-parsed anchor's own .text() already
        # decodes a normal, single-escaped entity correctly (verified live) - this exists for the site-side
        # DOUBLE-escaping bug found live (a real anchor's raw HTML literally has '&amp;quot;', which any
        # correct parser only unescapes ONE level, leaving a literal '&quot;' as real text content); calling
        # decode_entities again here is a safe no-op for every already-clean title (see ajp.extract.text's
        # own idempotency contract), never applied twice on the SAME still-encoded text.
        title = decode_entities(it.title)
        teaser = decode_entities(it.teaser) if it.teaser else it.teaser
        # T34 (packet 018/020, PM-verified defect, 33 real rows) / T35b (packet 021, PM-verified defect, 24
        # more real rows): the discovery page's own anchor text could come out garbled (medf.kg.ac.rs serves
        # windows-1251, fixed at the root above - this stays as a backstop for the next server that
        # mis-declares its own charset) OR be the ENTIRE aggregated board announcement rather than one call's
        # own title (medf.kg.ac.rs's own real example again: a ~10k-char wall covering many separate calls).
        # Title resolution order, exactly as PM's own fix specifies: (1) the anchor text (already the case,
        # when clean and reasonably title-shaped) -> (2) the URL's own humanized filename -> (3) an honest
        # placeholder.
        if _has_garbled_text(title) or _title_is_oversized_or_listlike(title):
            title = filename_title(it.url) or "Untitled call (scanned document)"
            if _has_garbled_text(title) or _title_is_oversized_or_listlike(title):
                title = "Untitled call (scanned document)"  # defensive-only: keep()'s own gate already
                # requires a real classification signal in title+filename+context before a row is ever
                # created at all, so a genuinely all-garbled/all-oversized item this branch alone would fix
                # is never actually reached through the real discovery pipeline - kept anyway for a future
                # classification path that might not require this specific signal.
        if teaser and _has_garbled_text(teaser):
            teaser = None    # never store garbled text as the teaser/raw_text either
        pk, rs_title = rs_text.position_kind(title)
        row: Dict[str, Any] = {
            "ext_uid": hashlib.sha1(it.url.encode()).hexdigest()[:16], "source_id": source_id, "url": it.url, "title_raw": title,
            "org_id": org.get("id"), "org_raw": org.get("name_native") or org.get("name_en"), "country_iso2": "RS", "city": org.get("city"),
            "position_kind": pk, "kind": kind, "ad_lang": rs_text.script_of(title), "raw_text": teaser or title,
            # R5: a military/police employer defaults foreigner_eligible='no' unless the ad text explicitly states
            # otherwise - listing-level text alone (title/teaser; documents are never opened) essentially never
            # does, so this is the org-level default from its own seed entry, 'unknown' for every other org.
            # a DB-sourced org dict (crawl_targets()) always HAS this key, just often with value None (SQL NULL) -
            # 'or' catches that the same as a missing key, unlike a two-arg dict.get() default which would not.
            "raw_html_ref": raw_ref, "foreigner_eligible": org.get("foreigner_eligible_default") or "unknown", "extraction_family": it.family,
            "apply_url": it.url,   # T26 (packet 015): a real, always-clickable link even before any detail
                                    # enrichment - _enrich_deadlines below may still add contacts on top.
        }
        if it.posted:
            row["posted_at"] = datetime(it.posted.year, it.posted.month, it.posted.day, tzinfo=timezone.utc)
            if it.date_basis:      # 'filename' or 'visible_text' (parity ruling); a directly stated date (<time>, article/wp_permalink text) has no basis - it needs no support (D6)
                row.update(posted_at_basis=it.date_basis, posted_at_conf=it.date_conf, posted_at_raw=it.date_raw)
            stale_after = (org.get("crawl_config") or {}).get("stale_after_days", STALE_AFTER_DAYS)
            if (now - row["posted_at"]).days > stale_after:
                row["status"] = "expired"        # display only (see module docstring); no deadline exists at listing level to say otherwise
        else:
            row["status"] = "unknown"            # cannot judge staleness without any date; never defaults to 'open'
        if rs_title:
            row["rs_research_title"] = rs_title
        if (ct := rs_text.contract_type(title)):
            row["contract"] = ct["v"]
        if (dur := rs_text.duration_months(title)):
            row.update(duration_months_v=dur["v"], duration_months_conf=dur["conf"], duration_months_raw=dur["raw"])
        rows.append(row)
        stats[kind] = stats.get(kind, 0) + 1
    return rows, stats


def looks_like_js_shell(html: str) -> bool:
    """A page that is mostly scripts and an empty mount point: httpx sees no content, a browser would."""
    t = HTMLParser(html)
    scripts = len(t.css("script"))
    for n in t.css("script, style, noscript"):
        n.decompose()
    text = _clean(t.body.text(separator=" ")) if t.body else ""
    mount = re.search(r"""id=["'](?:root|app|__next|__nuxt)["']""", html)
    return len(text) < 400 and (scripts >= 2 or bool(mount))


_PAGINATION_HREF = re.compile(r"[?&](?:page|paged|start|pg|p)=\d+|/page/\d+|/strana/\d+|/str/\d+", re.I)


def next_page_url(html: str, url: str, page_no: int) -> Optional[str]:
    """rel=next first; then a 'load more' button (an attribute/class naming it as such, whatever its own label
    says - 'Učitaj više' names nothing about page numbers); otherwise the numbered pager link whose text IS the
    next page number."""
    tree = HTMLParser(html)
    link = tree.css_first('link[rel="next"]') or tree.css_first('a[rel="next"]')
    if link is not None and link.attributes.get("href"):
        return urljoin(url, link.attributes["href"])
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        classes = (a.attributes.get("class") or "").lower()
        if href and _PAGINATION_HREF.search(href) and ("loadmore" in classes.replace("-", "").replace("_", "")
                                                        or "data-fs-loadmore-button" in (a.html or "")):
            return urljoin(url, href)
    target = str(page_no + 1)
    for a in tree.css("a[href]"):
        href = a.attributes.get("href", "")
        if _clean(a.text()) == target and _PAGINATION_HREF.search(href):
            return urljoin(url, href)
    return None


def _detail_own_date(html: str) -> Optional[date]:
    """The detail page's own posted/published date, if it states one: a <time> element first (datetime attribute,
    then its visible text), else the same dd.mm.yyyy pattern used elsewhere in this module."""
    t = HTMLParser(html).css_first("time")
    if t is not None:
        d = _date_from("", t.attributes.get("datetime") or "", "")
        if d:
            return d
        m = _DMY.search(_clean(t.text()))
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass
    return None


def deadline_from_detail_page(
    html: str, *, fallback_posted: Optional[datetime] = None, now: Optional[datetime] = None,
) -> Optional[Tuple[datetime, str]]:
    """(deadline, status) if the page states the explicit 'rok ... N dana od dana objavljivanja/oglasavanja' rule
    AND a date to count it from is available (the page's own posted date, else the listing's); otherwise None -
    a deadline is never computed from an assumed anchor date (D6)."""
    anchor = _detail_own_date(html) or (fallback_posted.date() if fallback_posted else None)
    if anchor is None:
        return None
    body = HTMLParser(html).body
    dd = rs_text.deadline_days(_clean(body.text(separator=" ")) if body else html)
    if dd is None:
        return None
    now = now or datetime.now(timezone.utc)
    end = (datetime(anchor.year, anchor.month, anchor.day, 23, 59, 59, tzinfo=_BELGRADE) + timedelta(days=dd["days"])).astimezone(timezone.utc)
    return end, ("expired" if end <= now else "open")


class BoardConnector(Connector):
    source_id = "rs_org_boards"
    kind = "scrape_html"

    def __init__(self, client, store, org: Dict[str, Any], *, renderer_factory=None, state: Optional[CrawlState] = None):
        if org.get("render_mode") in ("deeplink_only", "skip"):
            raise TypeError(f"{org.get('slug')}: render_mode={org['render_mode']} - this organisation is never fetched")
        self.org, self.state = org, state
        hosts = [urlsplit(u).netloc for u in [org.get("homepage")] + list(org.get("career_urls") or []) if u]
        self.manifest = OrgManifest.for_hosts(hosts)
        self._renderer_factory, self._renderer = renderer_factory, None
        super().__init__(client, store)

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('rs_org_boards','scrape_html','https://www.bg.ac.rs/',true,1.0,'RS',
                                'Direct organisation boards (faculties/institutes): verified direct org pages; one row per announcement; org_id set')
                        ON CONFLICT (id) DO NOTHING""")

    # -- fetching ---------------------------------------------------------------------------
    def renderer(self):
        if self._renderer is None and self._renderer_factory is not None:
            self._renderer = self._renderer_factory(self.client, self.manifest)
        return self._renderer

    def close(self) -> None:
        if self._renderer is not None:
            self._renderer.close()

    def _render(self, url: str):
        res = self.renderer().render(url)
        ref = put_raw(self.store, kind="html", source_id=self.source_id, url=url, content=res.content, fetched_at=res.fetched_at,
                      status=res.status, content_type=res.content_type, final_url=res.final_url, robots_allowed=True,
                      extra={"render": "playwright"})
        return res, ref

    def _fetch_page(self, url: str):
        """(result, raw ref, rendered). httpx first; the browser only for render_mode=playwright orgs, for connection-level
        errors such as legacy TLS (R2), or when the page turns out to be an empty JS shell. A robots/manifest refusal is never
        worked around by rendering."""
        if self.org.get("render_mode") == "playwright" and self._renderer_factory:
            return (*self._render(url), True)
        try:
            res, ref = self.fetch_raw(url)
            return res, ref, False
        except (RobotsDenied, ManifestViolation):
            raise
        except FetchFailed as e:
            if self._renderer_factory and is_transport_error(e):
                return (*self._render(url), True)
            raise

    # -- crawling ---------------------------------------------------------------------------
    def _crawl_url(self, url: str, max_pages: int, rows: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
        seen_pages, seen_items, cur = set(), set(), url
        for page_no in range(1, max_pages + 1):
            seen_pages.add(cur)
            try:
                res, ref, rendered = self._fetch_page(cur)
            except (ManifestViolation, RobotsDenied):
                if page_no == 1:
                    raise
                break                                        # a pager link leading off-domain / into a robots deny just ends paging
            if res.status != 200:
                if page_no == 1:
                    raise FetchFailed(f"HTTP {res.status} for {cur}")
                break
            html = _decode_html(res.content, res.content_type)
            cfg = self.org.get("crawl_config") or {}
            if urlsplit(cur).path.endswith(".xml") and cfg.get("sitemap_include"):
                # va.mod.gov.rs: the sitemap itself IS the board (see parse_sitemap_items) - never paginated,
                # never rendered (a flat XML file is not a JS shell).
                items = parse_sitemap_items(html, include=cfg["sitemap_include"], exclude=cfg.get("sitemap_exclude", ()))
            else:
                items = parse_items(html, res.final_url)
                if not items and not rendered and self._renderer_factory and looks_like_js_shell(html):
                    res, ref = self._render(cur)
                    html, rendered = _decode_html(res.content, res.content_type), True
                    items = parse_items(html, res.final_url)
            report["pages_fetched"] += 1
            report["rendered"] += int(rendered)
            new = [i for i in items if i.url not in seen_items]
            seen_items.update(i.url for i in new)
            if page_no > 1 and not new:
                break
            r, st = rows_from_items(new, self.org, ref.key, self.source_id)
            report["items"] += st["items"]
            report["excluded_person_specific"] += st["excluded_person_specific"]
            rows += r
            if urlsplit(cur).path.endswith(".xml") and cfg.get("sitemap_include"):
                break                                            # a sitemap is one page, never follow next_page_url on it
            nxt = next_page_url(html, res.final_url, page_no)
            if not nxt or nxt in seen_pages:
                break
            cur = nxt

    def crawl(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Every known board URL of the organisation, up to crawl_config.max_pages pages each (default 1, hard cap 10)."""
        cfg = self.org.get("crawl_config") or {}
        max_pages = max(1, min(int(cfg.get("max_pages", 1)), MAX_PAGES_HARD))
        rows: List[Dict[str, Any]] = []
        report: Dict[str, Any] = {"slug": self.org.get("slug"), "max_pages": max_pages, "pages_fetched": 0, "rendered": 0,
                                  "items": 0, "rows": 0, "excluded_person_specific": 0, "blocked": []}
        for url in self.org.get("career_urls") or []:
            already = self.state.blocked_reason(url) if self.state else None
            if already:
                report["blocked"].append({"url": url, "reason": f"blocked earlier: {already}"})
                continue
            try:
                before = len(rows)
                self._crawl_url(url, max_pages, rows, report)
                if cfg.get("empty_is_failure") and len(rows) == before:
                    # the page loads but nothing usable is on it (empty, JS shell that stays empty, or only excluded
                    # person-specific notices): counted by the R1 run counter, deeplink after the 3rd consecutive run
                    raise FetchFailed("page loaded but no listing rows (nothing open / classifiable)")
            except Exception as e:  # noqa: BLE001 - every failure is reported with its reason, never skipped silently
                del rows[before:]
                reason = f"{type(e).__name__}: {str(e)[:100]}"
                permanent = isinstance(e, RobotsDenied) and e.reason == "disallowed"
                entry = {"url": url, "reason": reason}
                if self.state:
                    entry.update(self.state.fail(url, reason, permanent))
                report["blocked"].append(entry)
            else:
                if self.state:
                    self.state.ok(url)
        report["rows"] = len(rows)
        self._enrich_deadlines(rows, report)
        self._enrich_documents(rows, report)
        return rows, report

    # -- detail-page deadline + contact enrichment --------------------------------------------
    DEADLINE_MAX_BYTES = 5 * 1024 * 1024

    def _enrich_deadlines(self, rows: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
        """At most one further request per kept employment row (its own URL), looking for the explicit Serbian
        legal-deadline sentence AND (T26, packet 015) any real contact email/phone/name the detail page
        states. 'article'/'wp_permalink' rows only - a 'document' row's URL is the attachment itself, handled
        separately by _enrich_documents below (T31), and an 'anchor' row's target is not reliably a single-ad
        detail page. A row whose deadline is already known (from the listing page's own text) still gets this
        fetch now - contacts are a separate concern from the deadline and must not be skipped just because the
        deadline already was found elsewhere; only the deadline-specific work inside the loop is itself
        skipped for such a row."""
        report["deadline_fetch_errors"] = 0
        report["deadlines_found"] = 0
        fetched: Dict[str, Optional[FetchResult]] = {}
        for row in rows:
            if row.get("kind") != "employment" or row.get("extraction_family") not in ("article", "wp_permalink"):
                continue
            url = row["url"]
            if url not in fetched:
                try:
                    res, _ = self.fetch_raw(url, max_bytes=self.DEADLINE_MAX_BYTES)
                    fetched[url] = res if res.status == 200 and "html" in (res.content_type or "text/html") else None
                except Exception:  # noqa: BLE001 - a detail fetch failing must not fail the row or the crawl
                    report["deadline_fetch_errors"] += 1
                    fetched[url] = None
            res = fetched[url]
            if res is None:
                continue
            html = _decode_html(res.content, res.content_type)
            if not row.get("deadline"):
                found = deadline_from_detail_page(html, fallback_posted=row.get("posted_at"))
                if found is not None:
                    row["deadline"], row["status"] = found
                    report["deadlines_found"] += 1
            contacts = extract_contacts(html=html, text=row.get("raw_text") or "")
            if contacts["contact_emails"] or contacts["contact_phones"] or contacts["contact_name"]:
                row.update(contacts)

    # -- document (.pdf/.docx/.doc) text ingestion ---------------------------------------------
    DOCUMENT_MAX_BYTES = 20 * 1024 * 1024        # a real konkurs PDF can be several MB; bigger than an HTML page
    _DOC_KIND_FOR_SUFFIX = {".pdf": "pdf", ".docx": "docx", ".doc": "doc"}

    def _enrich_documents(self, rows: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
        """T31 (packet 019): a 'document'-family row's own URL IS the attachment itself (591 of 824
        rs_org_boards rows point at one, PM's own recon) - downloaded ONCE (politeness/robots via
        self.fetch_raw, same manifest/rate-limit as every other request this connector makes), extracted
        (pdfplumber for PDF, python-docx for DOCX, macOS textutil best-effort for legacy .doc), and fed
        through the SAME builder pipeline an HTML detail page gets: contacts, contract/duration. A scanned
        or genuinely unreadable document keeps its own row and its own original link either way - never
        dropped (D6: OCR stays explicitly out of scope, never attempted) - only honestly flagged via the new
        content_scanned column. Title-derived contract/duration (rows_from_items, listing-text-only) is kept
        as-is when already present; a real document's own body text almost always states these even when the
        title never does, so this only ever ADDS what the title alone could not."""
        report.update(documents_attempted=0, documents_extracted=0, documents_scanned=0, documents_failed=0)
        for row in rows:
            if row.get("extraction_family") != "document" or row.get("kind") != "employment":
                continue
            suffix = Path(urlsplit(row["url"]).path).suffix.lower()
            doc_kind = self._DOC_KIND_FOR_SUFFIX.get(suffix)
            if doc_kind is None:
                continue
            report["documents_attempted"] += 1
            try:
                res, _ = self.fetch_raw(row["url"], kind=doc_kind, max_bytes=self.DOCUMENT_MAX_BYTES)
            except Exception:  # noqa: BLE001 - a document fetch failing must never fail the row or the crawl
                report["documents_failed"] += 1
                continue
            if res.status != 200:
                report["documents_failed"] += 1
                continue
            with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
                tmp.write(res.content)
                tmp.flush()
                extracted = extract_document_text(tmp.name, suffix)
            if extracted.extract_failed:
                report["documents_failed"] += 1
                continue
            row["content_scanned"] = extracted.content_scanned
            if extracted.content_scanned:
                report["documents_scanned"] += 1
                continue                             # OCR out of scope - keep the row+link, extract nothing further
            report["documents_extracted"] += 1
            text = extracted.text
            row["raw_text"] = text
            row["requirements_raw"] = text
            contacts = extract_contacts(text=text)
            if contacts["contact_emails"] or contacts["contact_phones"] or contacts["contact_name"]:
                row.update(contacts)
            if "contract" not in row and (ct := rs_text.contract_type(text)):
                row["contract"] = ct["v"]
            if "duration_months_v" not in row and (dur := rs_text.duration_months(text)):
                row.update(duration_months_v=dur["v"], duration_months_conf=dur["conf"], duration_months_raw=dur["raw"])


def crawl_orgs(conn, client, store, org_list: List[Dict[str, Any]], run_id: str, renderer_factory=None) -> List[Dict[str, Any]]:
    """One scheduled run over organisations: crawl, ingest, update the run-level retry counters (R1), and degrade an
    organisation to a deeplink channel only when every one of its board URLs is blocked."""
    BoardConnector.ensure_source(conn)
    state = CrawlState(conn, BoardConnector.source_id, run_id)
    reports = []
    for org in org_list:
        c = BoardConnector(client, store, org, renderer_factory=renderer_factory, state=state)
        try:
            rows, rep = c.crawl()
        finally:
            c.close()
        # F8 (found live wiring this into the scheduler, 2026-09-24): upsert_listing returns (listing_id,
        # inserted) - a 2-tuple, ALWAYS truthy regardless of `inserted`'s own value, so `if upsert_listing(...)`
        # previously counted every row as 'ingested' whether it was a fresh insert or an update to an
        # existing row. Unpacked properly: rows_new/rows_updated are now real, distinct counts.
        #
        # F14 (2026-10-11): the ingest call is upsert_listing_deduped, not plain upsert_listing. Every other
        # connector goes through the scheduler's own upsert_listing_deduped; this loop was the one exception,
        # and `sources` is deliberately NOT an insertable column (ingest.LISTING_COLUMNS excludes it as
        # "managed by upsert_listing_deduped"), so every row written here kept 0014's DEFAULT '[]' forever:
        # canonical_source() -> None, verified_direct False, the `source=` filter's
        # jsonb_array_elements(l.sources) never matched it, and /v1/facets' source dimension dropped it -
        # real, crawled listings that were invisible to both. (It also fed the crash fixed in ingest.py.)
        rows_new = rows_updated = 0
        for r in rows:
            _, action = upsert_listing_deduped(conn, r, store)
            if action == "inserted":
                rows_new += 1
            else:
                rows_updated += 1
        rep["ingested"] = rows_new + rows_updated
        rep["rows_new"] = rows_new
        rep["rows_updated"] = rows_updated
        urls = list(org.get("career_urls") or [])
        rep["degraded_to_deeplink"] = bool(urls and all(state.blocked_reason(u) for u in urls)
                                          and orgs_mod.degrade_to_deeplink(conn, org["slug"], "; ".join(sorted({state.blocked_reason(u) or "" for u in urls}))))
        reports.append(rep)
    return reports
