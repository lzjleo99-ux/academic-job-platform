"""Sljaka (sljaka.com), T13 wave A. A general (all-industry) Serbian jobs board, robots-allowed (verified
2026-09-22, plain WordPress robots.txt, no restrictions on /poslovi/feed/). Unlike an organisation's own board
(board.py), where the SOURCE itself is inherently academic, this aggregator carries every industry - PM's own
note: "observed beauty/retail + school teaching... strict academic classifier required, expect high discard
rate". Reuses nsz.py's exact, already-tested gating pattern for the identical problem (a general multi-industry
board): classify_kind() alone is not enough (ordinary bureaucratic hiring language - 'na odredjeno vreme' - is
common to EVERY employer, not just academic ones), so an 'employment' verdict is additionally required to pass
academic_ad() (a real academic POSITION noun) before being kept; enrollment/grant_call/internship markers are
specific enough on their own and are not re-gated (same reasoning as classify_kind's own docstring).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from ajp.connectors.base import Connector, UrlManifest
from ajp.connectors.jobposting import classify_location
from ajp.extract import rs_text
from ajp.extract.evidence import with_kind_override
from ajp.extract.text import decode_bytes
from ajp.extract_contacts import extract_contacts

HOST = "sljaka.com"
RSS_URL = f"https://{HOST}/poslovi/feed/"
MANIFEST = UrlManifest(rf"https://{HOST}/poslovi/feed/?")
_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TITLE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S)
_LINK = re.compile(r"<link>(.*?)</link>", re.S)
_PUBDATE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
_CONTENT = re.compile(r"<content:encoded><!\[CDATA\[(.*?)\]\]></content:encoded>", re.S)
_HTML_TAG = re.compile(r"<[^>]+>")


def parse_rss(xml_text: str) -> List[Dict[str, Any]]:
    """title/link/pubDate/body for each RSS item - unlike CERN's feed, Sljaka's own <content:encoded> carries
    the FULL ad text (live-verified), so no separate detail fetch is needed to classify."""
    items = []
    for block in _ITEM.findall(xml_text):
        title_m, link_m = _TITLE.search(block), _LINK.search(block)
        if not (title_m and link_m):
            continue
        pub_m, body_m = _PUBDATE.search(block), _CONTENT.search(block)
        body_html = body_m.group(1) if body_m else ""
        body = _HTML_TAG.sub(" ", body_html)
        body = re.sub(r"\s+", " ", body).strip()
        posted = None
        if pub_m:
            try:
                posted = parsedate_to_datetime(pub_m.group(1).strip())
                if posted.tzinfo is None:
                    posted = posted.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                posted = None
        items.append({"title": title_m.group(1).strip(), "link": link_m.group(1).strip(),
                      "posted": posted, "body": body, "body_html": body_html})
    return items


def classify(title: str, body: str = "") -> str:
    """The nsz.py pattern, adapted for one real difference: NSZ's own structured 'position' field states the
    employment shape directly, but Sljaka's RSS <title> is often a bare role name ('Docent za oblast
    Informatika') with the actual 'raspisuje konkurs za izbor u zvanje...' framing only in the body - found
    live, so the kind SHAPE is judged on title+body together. academic_ad's position-noun check stays on the
    title alone (employer is unknown from the RSS alone, so its own employer-string gate simply never fires,
    which is fine: 'DOMAR'/'Kuvar'/etc fail the position-noun check regardless of who's hiring)."""
    return classify_fields(title, body)["kind"]


def classify_fields(title: str, body: str = "") -> Dict[str, Any]:
    """``classify`` carrying the classifier's own {conf, raw} evidence (T48, packet 028). The academic_ad
    gate below changes the kind the classifier produced, so its evidence is dropped along with it - an
    excerpt for a decision that was overruled is not provenance (D6)."""
    f = rs_text.kind_row_fields(f"{title} {body}", title)
    if f["kind"] == "employment" and not rs_text.academic_ad(title):
        return with_kind_override(f, "other")
    return f


class SljakaConnector(Connector):
    source_id = "sljaka"
    kind = "feed"
    manifest = MANIFEST

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('sljaka','feed',%s,%s,true,1.0,'RS',
                                'General (all-industry) Serbian jobs board RSS - 10 latest; strict academic '
                                'gate applied on top of classify_kind (nsz.py pattern), high discard rate is '
                                'expected and correct, not a bug')
                        ON CONFLICT (id) DO NOTHING""", (f"https://{HOST}/", RSS_URL))

    def crawl(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        res, ref = self.fetch_raw(RSS_URL, kind="xml")
        items = parse_rss(decode_bytes(res.content, res.content_type))
        rows: List[Dict[str, Any]] = []
        report = {"items": len(items), "kept": 0, "discarded": 0}
        for it in items:
            kind_f = classify_fields(it["title"], it["body"])
            kind = kind_f["kind"]
            if kind == "other":
                report["discarded"] += 1
                continue
            report["kept"] += 1
            pk, rs_title = kind_f["position_kind"], kind_f.pop("rs_research_title", None)
            location = classify_location(f"{it['title']} {it['body']}", has_country=True)
            contacts = extract_contacts(html=it["body_html"], text=it["body"])
            row: Dict[str, Any] = {
                "ext_uid": hashlib.sha1(it["link"].encode()).hexdigest()[:16], "source_id": self.source_id,
                "url": it["link"], "title_raw": it["title"], "org_raw": None, "country_iso2": "RS",
                **kind_f, "ad_lang": rs_text.script_of(it["title"]),
                "raw_text": it["body"][:2000], "raw_html_ref": ref.key, "foreigner_eligible": "unknown",
                "extraction_family": "sljaka_rss",
                "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
                "location_class_raw": location["location_class_raw"],
                "apply_url": it["link"], **contacts,
            }
            if rs_title:
                row["rs_research_title"] = rs_title
            if it["posted"]:
                row["posted_at"] = it["posted"]
            else:
                row["status"] = "unknown"
            rows.append(row)
        return rows, report
