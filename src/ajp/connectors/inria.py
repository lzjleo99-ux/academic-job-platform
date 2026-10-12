"""Inria (French national research institute for computer science), T19. jobs.inria.fr - LIVE-VERIFIED
2026-09-23: robots.txt is a plain, fully-permissive file ("User-agent: *\\nDisallow:"). www.inria.fr is
EXCLUDED per the addenda (Anubis bot-challenge-protected - never bypassed, same discipline as HERC); this
subdomain is a genuinely separate host with its own permissive robots.txt and is the org's own real
recruitment site, not a workaround for anything.

The list page (/public/classic/en/offres) shows every open posting on ONE page (confirmed live: its own
"116 offres" count matches the number of unique detail links found - no pagination needed). Requesting the
/en/ path gives /en/-prefixed detail links throughout; each detail page's own real structured "General
Information" block (Theme/Domain, Town/city, Inria Center, Starting date, Duration of contract, Deadline to
apply) is used directly - country is a fixed org fact (Inria is a French national institute; every one of
its centres, per its own "Inria Center" field, is within France), never guessed from free text.

Real, live-verified language finding: even the /en/-path detail page keeps some ads' own body genuinely in
French (a real banner says so: "The offer description below is in French" - and admin/support roles at
Inria are routinely French-only, unlike its research titles which are bilingual) - the ad's own <h1
lang="fr"> attribute (present only on those French-only ads) is the real, non-guessed signal used to set
ad_lang, not an assumption that requesting /en/ guarantees English content.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from selectolax.parser import HTMLParser

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, UrlManifest
from ajp.connectors.jobposting import classify_location
from ajp.extract.intl_text import kind_row_fields
from ajp.extract.text import decode_bytes
from ajp.extract_contacts import extract_contacts
from ajp.robots import RobotsDenied

HOST = "jobs.inria.fr"
LIST_URL = f"https://{HOST}/public/classic/en/offres"
ORG_RAW = "Inria"
COUNTRY = "FR"

MANIFEST = UrlManifest(
    re.escape(LIST_URL),
    rf"https://{re.escape(HOST)}/public/classic/(en|fr)/offres/[\w-]+/?",
)

# requires at least one digit in the id segment - the list page also links a non-posting "/offres/map"
# utility page (a real sitemap-style page, found live) that would otherwise be discovered as a fake row.
_DETAIL_LINK = re.compile(r'href="(/public/classic/en/offres/[\w-]*\d[\w-]*)"')
_GENERAL_INFO_LABEL = re.compile(r"^(.+?)\s*:\s*$")
# listing.contract's own CHECK enum (0003_listing.sql: fixed/permanent/grant/fellowship/phd_enrolment/
# service_contract) - a controlled value, never the page's own free-text "Contract type" string passed
# through directly. Found live (T21, the first real ajp-run --once against a real seed): a genuine
# CheckViolation on ingest, since this connector had never actually been run through upsert_listing before
# (T19's own testing was connector-level only - discover()+crawl(), never the full scheduler pipeline).
# Only the one value confirmed live so far ("Fixed-term contract") plus its obvious counterpart are mapped;
# anything else stays unmapped (None) rather than guessed.
_CONTRACT_MAP = {"fixed-term contract": "fixed", "permanent contract": "permanent", "permanent position": "permanent"}


def _map_contract(raw: Optional[str]) -> Optional[str]:
    return _CONTRACT_MAP.get((raw or "").strip().lower())


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_list(html: str) -> List[str]:
    """The list page's own /en/-prefixed detail links, deduplicated (the same link legitimately appears
    twice on this page - once in a card, once in a 'read more' anchor)."""
    seen, out = set(), []
    for m in _DETAIL_LINK.finditer(html):
        path = m.group(1)
        if path not in seen:
            seen.add(path)
            out.append(f"https://{HOST}{path}")
    return out


def parse_detail(html: str) -> Dict[str, Any]:
    """h1 (title + its own lang attribute, the real ad_lang signal) + the 'General Information' label/value
    list + the remaining page body (context/description sections) for classification and raw_text. Never
    guessed: a label this ad doesn't carry (an admin role with no 'Deadline to apply', say) simply leaves
    that field null, same D6 convention as everywhere else."""
    tree = HTMLParser(html)
    for tag in tree.css("script, style"):
        tag.decompose()
    h1 = tree.css_first("h1.h1-detail")
    title = _clean(h1.text()) if h1 else ""
    ad_lang = "fr" if (h1 and h1.attributes.get("lang") == "fr") else "en"
    # Two distinct label/value markup shapes carry the fields this connector needs, found live: the top
    # "Contract type"/"Level of qualifications required"/"Fonction" trio is <p class="list-group-item-text">
    # <b>Label : </b>Value</p> inside .item-offre, while "Town/city"/"Inria Center"/"Deadline to apply" are
    # <li><strong>Label :</strong>Value</li> inside the separate .red-test "General Information" block -
    # both parsed into the same flat dict.
    fields: Dict[str, str] = {}
    for el in tree.css(".item-offre p.list-group-item-text"):
        label_el = el.css_first("b")
        if not label_el:
            continue
        label_text = _clean(label_el.text())
        m = _GENERAL_INFO_LABEL.match(label_text)
        if not m:
            continue
        full_text = _clean(el.text())
        fields[m.group(1)] = _clean(full_text[len(label_text):]) if full_text.startswith(label_text) else full_text
    for li in tree.css(".red-test li"):
        strong = li.css_first("strong")
        if not strong:
            continue
        strong_text = _clean(strong.text())
        m = _GENERAL_INFO_LABEL.match(strong_text)
        if not m:
            continue
        label = m.group(1)
        full_text = _clean(li.text())
        value = _clean(full_text[len(strong_text):]) if full_text.startswith(strong_text) else full_text
        fields[label] = value
    body_els = tree.css(".grand-item-offre") or ([tree.body] if tree.body else [])
    body_text = _clean(" ".join(el.text(separator=" ") for el in body_els))

    def _parse_iso_date(raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.strptime(raw.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    return {
        "title": title, "ad_lang": ad_lang, "body_text": body_text,
        "city": fields.get("Town/city") or None,
        "centre": fields.get("Inria Center") or None,
        "contract_raw": fields.get("Contract type") or None,
        "contract": _map_contract(fields.get("Contract type")),
        "deadline": _parse_iso_date(fields.get("Deadline to apply")),
        "posted_at": None,                          # this org's own page never states one (D6: left null)
    }


class InriaConnector(Connector):
    source_id = "inria"
    kind = "feed"
    manifest = MANIFEST

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('inria','feed',%s,%s,true,1.0,'FR',
                                'jobs.inria.fr - fully robots-permissive; www.inria.fr is EXCLUDED per the '
                                'addenda (Anubis bot-challenge-protected, never bypassed). List page shows '
                                'every open posting on one page (no pagination). Country fixed FR (a national '
                                'institute fact); city/deadline/contract from the detail page''s own '
                                '"General Information" structured block.')
                        ON CONFLICT (id) DO NOTHING""", (f"https://{HOST}/", LIST_URL))

    def discover(self) -> List[str]:
        res, _ = self.fetch_raw(LIST_URL)
        return parse_list(decode_bytes(res.content, res.content_type))

    def crawl(self, links: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(links), "parsed": 0, "kept": 0, "discarded": 0, "fetch_errors": 0}
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
            if not detail["title"]:
                report["fetch_errors"] += 1
                continue
            report["parsed"] += 1
            title, body = detail["title"], detail["body_text"]
            kind_f = kind_row_fields(title, body)
            kind = kind_f["kind"]
            if kind == "other":
                report["discarded"] += 1
                continue
            location = classify_location(f"{title} {body}", has_country=True)
            contacts = extract_contacts(html=html, text=body)
            row: Dict[str, Any] = {
                "ext_uid": link.rstrip("/").rsplit("/", 1)[-1], "source_id": self.source_id,
                "url": link, "title_raw": title, "org_raw": ORG_RAW, "country_iso2": COUNTRY,
                "city": detail["city"], **kind_f,
                "ad_lang": detail["ad_lang"], "raw_text": body[:2000], "ft_pt": "unknown",
                "contract": detail["contract"], "foreigner_eligible": "unknown",
                "extraction_family": "inria_html", "raw_html_ref": ref.key, "status": "open",
                "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
                "location_class_raw": location["location_class_raw"],
                "apply_url": link, **contacts,
            }
            if detail["deadline"]:
                row["deadline"] = detail["deadline"]
                if detail["deadline"] < datetime.now(timezone.utc):
                    row["status"] = "expired"
            rows.append(row)
            report["kept"] += 1
        return rows, report
