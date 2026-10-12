"""KTH Royal Institute of Technology (Sweden), T19. RSS discovery at kth.se/lediga-jobb/rss/ (PATH form) -
LIVE-VERIFIED 2026-09-23: robots.txt (only served at www.kth.se; the bare kth.se host 301s everything there,
including /robots.txt itself) explicitly disallows the QUERY form (`Disallow: /*?rss=*`) but not this path
form, matching the addenda's own finding exactly. Feed returns 200, real items.

Bespoke module, not gen_wordpress: this is Varbi (KTH's own recruitment ATS, kth.varbi.com - confirmed
detection-only at T18, no keyless feed/API on the ATS side itself), not a WordPress board at all - the feed
just happens to be a real RSS document KTH itself publishes on its own www.kth.se host as a discovery
convenience. Its own <description> is a bare "Läs mer på: {link}" stub, so a detail fetch is required.

REAL FINDING, live, that changed this module's own design mid-build: the feed's own <title> is always
Swedish (a static, server-generated document with no per-request language negotiation - confirmed: identical
whether fetched with or without an Accept-Language header), but the DETAIL PAGE negotiates language, and
this project's own PoliteClient always sends `Accept-Language: en` on every request (base.py) - so the detail
page this connector actually fetches is genuinely in ENGLISH ("Doctoral student in ...", "Location"/"Country"/
"Contract type" labels), not Swedish. The feed's own title is discarded in favour of the detail page's own H1
(the canonical title in the SAME language as the rest of the row's captured content - raw_text, labels), and
country is a plain English name pycountry resolves directly - no manual Swedish gazetteer needed at all, since
that is not the language this connector's own requests actually receive.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pycountry
from selectolax.parser import HTMLParser

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, UrlManifest
from ajp.connectors.jobposting import classify_location
from ajp.extract.intl_text import kind_row_fields
from ajp.extract.text import decode_bytes
from ajp.extract_contacts import extract_contacts
from ajp.robots import RobotsDenied

HOST = "www.kth.se"
LINK_HOST = "kth.se"                                     # the feed's own <link>/<guid> use the bare host, which
                                                          # 301s everything to www.kth.se - both are permitted.
FEED_URL = f"https://{HOST}/lediga-jobb/rss/"
ORG_RAW = "KTH Royal Institute of Technology"

MANIFEST = UrlManifest(
    re.escape(FEED_URL),
    rf"https://{re.escape(LINK_HOST)}/lediga-jobb/\d+/?",
    rf"https://{re.escape(HOST)}/lediga-jobb/\d+/?",
)

_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_LINK = re.compile(r"<link>(.*?)</link>", re.S)
_DETAILS_ROW = re.compile(r'<div class="details-list"><b>(.*?)</b><p>(.*?)</p></div>', re.S)
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}
_EN_DATE = re.compile(r"^(\d{1,2})\.([A-Za-z]{3})\.(\d{4})$")
_FT_PT = {"full time": "full", "part time": "part"}
# listing.contract's own CHECK enum (0003_listing.sql: fixed/permanent/grant/fellowship/phd_enrolment/
# service_contract) - a controlled value, never KTH's own free-text "Type of employment" string passed
# through directly. Found live (T21, the first real ajp-run --once against a real seed, the same bug class
# as inria.py's own): a genuine CheckViolation on ingest ("Temporary position" is not a valid enum value).
# Only the two real values seen live so far are mapped; anything else stays unmapped (None), never guessed.
_CONTRACT_MAP = {"temporary position": "fixed", "permanent position": "permanent"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _country_iso2(name: str) -> Optional[str]:
    try:
        return pycountry.countries.lookup(name.strip()).alpha_2
    except LookupError:
        return None


def _parse_en_date(raw: Optional[str]) -> Optional[datetime]:
    """KTH's own 'DD.Mon.YYYY' format (e.g. '24.Sep.2026') - parsed via a fixed EN month-abbreviation table,
    never str[ptime]'s locale-dependent %b (this process's own locale is not guaranteed to be English)."""
    if not raw:
        return None
    m = _EN_DATE.match(raw.strip())
    if not m:
        return None
    day, mon, year = m.groups()
    month = _MONTHS.get(mon[:3].title())
    if month is None:
        return None
    try:
        return datetime(int(year), month, int(day), tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_feed(xml_text: str) -> List[str]:
    """Discovery only - just the item links (a stable numeric-id URL each); the feed's own <title> is Swedish
    and unused (module docstring), its own <description> carries no content."""
    return [m.group(1).strip() for m in (_LINK.search(b) for b in _ITEM.findall(xml_text)) if m]


def parse_detail(html: str) -> Dict[str, Any]:
    """h1 (title, in whatever language this request's own response came back in) + h2 (department line) +
    the details-main-col body + the label/value metadata block. Never guessed: a label this org's own page
    doesn't carry (a future ad missing 'Country', say) simply leaves that field null, same D6 convention as
    everywhere else."""
    tree = HTMLParser(html)
    for tag in tree.css("script, style"):
        tag.decompose()
    title_el = tree.css_first("main h1")
    title = _clean(title_el.text()) if title_el else ""
    dept_el = tree.css_first("main h2")
    department = _clean(dept_el.text()) if dept_el else None
    body_el = tree.css_first(".details-main-col") or tree.css_first("main")
    body_text = _clean(body_el.text(separator=" ")) if body_el else ""
    fields = {_clean(k): _clean(v) for k, v in _DETAILS_ROW.findall(html)}
    city = fields.get("Location") or None
    country = _country_iso2(fields.get("Country", "")) if fields.get("Country") else None
    ft_pt = _FT_PT.get((fields.get("Contract type") or "").lower(), "unknown")
    contract_raw = fields.get("Type of employment") or None
    contract = _CONTRACT_MAP.get((contract_raw or "").lower())
    return {
        "title": title, "department": department, "body_text": body_text, "city": city,
        "country_iso2": country, "ft_pt": ft_pt, "contract": contract, "contract_raw": contract_raw,
        "deadline": _parse_en_date(fields.get("Last application date")),
        "posted_at": _parse_en_date(fields.get("Published")),
    }


class KthConnector(Connector):
    source_id = "kth"
    kind = "feed"
    manifest = MANIFEST

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('kth','feed',%s,%s,true,1.0,'SE',
                                'KTH RSS - PATH form only (/lediga-jobb/rss/); the query form (?rss=1) is '
                                'robots-disallowed for this host (Disallow: /*?rss=*), never used. Detail pages '
                                'are captured in English (this project''s own Accept-Language: en header is '
                                'honoured by the site), not the feed''s own Swedish - title_raw/raw_text/ad_lang '
                                'all reflect what was actually fetched. Varbi (the ATS this feed is fed from) '
                                'stays detection-only per T18 - no keyless feed/API on the ATS side itself.')
                        ON CONFLICT (id) DO NOTHING""", (f"https://{HOST}/", FEED_URL))

    def discover(self) -> List[str]:
        res, _ = self.fetch_raw(FEED_URL, kind="xml")
        return parse_feed(decode_bytes(res.content, res.content_type))

    def crawl(self, links: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(links), "parsed": 0, "kept": 0, "discarded": 0, "no_country": 0, "fetch_errors": 0}
        for link in links:
            try:
                res, ref = self.fetch_raw(link)
            except (FetchFailed, ManifestViolation, RobotsDenied):
                report["fetch_errors"] += 1
                continue
            if res.status != 200:
                report["fetch_errors"] += 1
                continue
            html = decode_bytes(res.content, res.content_type)
            detail = parse_detail(html)
            # T23 (PM ruling R2): an unresolvable country is still never guessed (D6 unchanged) - the row is
            # persisted with country_iso2=None + a real location_class now (migration 0020), not discarded.
            report["parsed"] += 1
            title, body = detail["title"], detail["body_text"]
            kind_f = kind_row_fields(title, body)
            kind = kind_f["kind"]
            if kind == "other":
                report["discarded"] += 1
                continue
            location = classify_location(f"{title} {body}", has_country=detail["country_iso2"] is not None)
            contacts = extract_contacts(html=html, text=body)
            row: Dict[str, Any] = {
                "ext_uid": link.rstrip("/").rsplit("/", 1)[-1], "source_id": self.source_id,
                "url": link, "title_raw": title, "org_raw": ORG_RAW,
                "country_iso2": detail["country_iso2"], "city": detail["city"],
                **kind_f, "ad_lang": "en",
                "raw_text": body[:2000], "ft_pt": detail["ft_pt"], "contract": detail["contract"],
                "foreigner_eligible": "unknown", "extraction_family": "kth_rss_html",
                "raw_html_ref": ref.key, "status": "open",
                "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
                "location_class_raw": location["location_class_raw"],
                "apply_url": link, **contacts,
            }
            if detail["deadline"]:
                row["deadline"] = detail["deadline"]
                if detail["deadline"] < datetime.now(timezone.utc):
                    row["status"] = "expired"
            if detail["posted_at"]:
                row["posted_at"] = detail["posted_at"]
            rows.append(row)
            report["kept"] += 1
        return rows, report
