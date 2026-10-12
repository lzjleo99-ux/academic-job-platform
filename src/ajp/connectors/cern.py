"""CERN (careers.cern), T13 wave A. robots.txt VERIFIED FINDING (2026-09-22): the SmartRecruiters JSON API PM's
own route spec named (api.smartrecruiters.com/v1/companies/CERN/postings) is explicitly DISALLOWED for a
general crawler - its robots.txt is 'User-agent: LinkedInBot: Allow: /v1/companies/' then 'User-agent: *:
Disallow: /'. This project's own, honestly-identified User-Agent is never LinkedInBot and falls under the '*'
group, which is denied outright - the API is NOT used, whatever the 'keyless public API' framing suggested;
PM's own instruction to 'verify its robots/ToS at build' is exactly what surfaced this. The RSS feed
(careers.cern/jobs/feed/, a completely different host, robots-allowed, plain WordPress robots.txt) is the real
discovery route, promoted from 'fallback' to primary by this finding - it lists the 10 most recent postings
only (no job-specific sitemap exists on this host, checked live: /sitemap.xml, /wp-sitemap.xml and the common
Yoast job_offer-sitemap.xml guess are all either the general pages sitemap or 404 - a real, disclosed
'last 10 only' limitation of this source, not a bug). Detail pages carry no schema.org JobPosting JSON-LD (a
custom WordPress job-board theme), so this module owns its own small HTML extraction, not jobposting.py's.
"""
from __future__ import annotations

import hashlib
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

HOST = "careers.cern"
RSS_URL = f"https://{HOST}/jobs/feed/"
MANIFEST = UrlManifest(
    rf"https://{HOST}/jobs/feed/?",
    rf"https://{HOST}/jobs/[^?#\s]+/?",
)
_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_LINK = re.compile(r"<link>(.*?)</link>", re.S)
_PUBDATE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
_DEADLINE = re.compile(r"Before\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+at\s+(\d{1,2}):(\d{2})", re.I)


def _country_iso2(name: str) -> Optional[str]:
    try:
        return pycountry.countries.lookup(name.strip()).alpha_2
    except LookupError:
        return None


def parse_rss(xml_text: str) -> List[Dict[str, str]]:
    """title/link/pubDate for each RSS item - the feed's own <description>/<content:encoded> are empty on this
    site (live-verified), so this is discovery only; the real content comes from crawl()'s detail fetch."""
    items = []
    for block in _ITEM.findall(xml_text):
        title_m, link_m = _TITLE.search(block), _LINK.search(block)
        if not (title_m and link_m):
            continue
        pub_m = _PUBDATE.search(block)
        items.append({"title": title_m.group(1).strip(), "link": link_m.group(1).strip(),
                      "pub_date": pub_m.group(1).strip() if pub_m else ""})
    return items


def parse_detail(html: str) -> Dict[str, Any]:
    """location (-> country/city) + full body text + a stated application deadline, from the detail page's own
    markup (no JSON-LD on this site) - deadline via the site's own fixed 'Before DD/MM/YYYY at HH:MM (Geneva
    Time)' phrasing, D6: nullable, never guessed from anything else."""
    tree = HTMLParser(html)
    loc_el = tree.css_first(".location")
    location = loc_el.text().strip() if loc_el else ""
    city, country = None, None
    if "," in location:
        city_part, country_part = location.rsplit(",", 1)
        city, country = city_part.strip(), _country_iso2(country_part.strip())
    for tag in tree.css("script, style"):
        tag.decompose()                    # Node.text() otherwise includes JS/CSS source as if it were prose
    main = tree.css_first("main") or tree.css_first("article") or tree.body
    body_text = re.sub(r"\s+", " ", main.text(separator=" ")).strip() if main else ""
    deadline = None
    m = _DEADLINE.search(body_text)
    if m:
        day, month, year, hh, mm = (int(g) for g in m.groups())
        try:
            deadline = datetime(year, month, day, hh, mm, tzinfo=timezone.utc)
        except ValueError:
            deadline = None
    return {"city": city, "country_iso2": country, "body_text": body_text, "deadline": deadline}


class CernConnector(Connector):
    source_id = "cern"
    kind = "feed"
    manifest = MANIFEST

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('cern','feed',%s,%s,true,1.0,'CH',
                                'CERN careers RSS (10 latest postings - no job-specific sitemap exists on this '
                                'host); the SmartRecruiters JSON API PM originally proposed is robots-disallowed '
                                'for a general crawler (LinkedInBot-only allow) and is never used')
                        ON CONFLICT (id) DO NOTHING""", (f"https://{HOST}/", RSS_URL))

    def discover(self) -> List[Dict[str, str]]:
        res, _ = self.fetch_raw(RSS_URL, kind="xml")
        return parse_rss(decode_bytes(res.content, res.content_type))

    def crawl(self, items: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(items), "parsed": 0, "kept": 0, "discarded": 0, "no_country": 0, "fetch_errors": 0}
        for item in items:
            # F7-class fix (found live wiring T26 contacts into this connector, 2026-09-25 - missed in the
            # original T23 F7 sweep, which only covered the shared jobposting.py root + gen_sitemap_html/
            # cnrs): one detail URL's fetch failing after retries must never abort the whole sweep.
            try:
                res, ref = self.fetch_raw(item["link"])
            except (FetchFailed, ManifestViolation, RobotsDenied):
                report["fetch_errors"] += 1
                continue
            if res.status != 200:
                continue
            html = decode_bytes(res.content, res.content_type)
            detail = parse_detail(html)
            # T23 (PM ruling R2): an unresolvable country is still never guessed (D6 unchanged) - the row is
            # persisted with country_iso2=None + a real location_class now (migration 0020), not discarded.
            report["parsed"] += 1
            kind_f = kind_row_fields(item["title"], detail["body_text"])
            kind = kind_f["kind"]
            location = classify_location(f"{item['title']} {detail['body_text']}",
                                         has_country=detail["country_iso2"] is not None)
            contacts = extract_contacts(html=html, text=detail["body_text"])
            row: Dict[str, Any] = {
                "ext_uid": hashlib.sha1(item["link"].encode()).hexdigest()[:16],
                "source_id": self.source_id, "url": item["link"], "title_raw": item["title"],
                "org_raw": "CERN", "country_iso2": detail["country_iso2"], "city": detail["city"],
                **kind_f,
                "ad_lang": "en", "raw_text": detail["body_text"][:2000], "ft_pt": "unknown",
                "foreigner_eligible": "unknown", "extraction_family": "cern_rss_html",
                "raw_html_ref": ref.key, "status": "unknown", "apply_url": item["link"], **contacts,
                "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
                "location_class_raw": location["location_class_raw"],
            }
            if detail["deadline"]:
                row["deadline"] = detail["deadline"]
                if detail["deadline"] < datetime.now(timezone.utc):
                    row["status"] = "expired"
                else:
                    row.pop("status")           # a real future deadline: default 'open' governs
            rows.append(row)
            if kind in ("employment", "grant_call", "internship", "enrollment"):
                report["kept"] += 1
            else:
                report["discarded"] += 1
        if report["attempted"] > 0 and report["fetch_errors"] == report["attempted"]:
            raise FetchFailed(f"all {report['attempted']} detail fetches failed for {self.source_id}")
        return rows, report
