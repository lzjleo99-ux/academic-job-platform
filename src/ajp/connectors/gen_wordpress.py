"""T18 gen_wordpress: a generic, config-driven connector for any WordPress-hosted job board - one engine,
many sites, instead of a bespoke module per site (packet 009's own architecture_ruling). Discovery tries, in
order, every path a WordPress site conventionally publishes: /feed/, /?feed=rss2, a category-specific feed,
then /wp-sitemap.xml - each robots-gated independently (packet 009: 'gate every feed path; infostud showed
feeds can be disallowed even on WP-like boards', found live at T13).

Real live-verified examples this engine's shape is drawn from: careers.cern/jobs/feed/ (custom 'job_offer'
post type, RSS content:encoded is empty - detail page has the real body) and sljaka.com/feed/ (WordPress
7.1.2, confirmed live 2026-09-23). Both already have their own richer, purpose-built connectors
(cern.py uses this exact shape already; sljaka.py additionally does RS-specific classification via
rs_text.py) - this module is for the NEXT WordPress board neither of those covers, discovered by T20's
fan-out, not a replacement for either existing one (registering the same host under two source ids would
double-ingest it).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from psycopg.types.json import Jsonb

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, ObjectStore, PoliteClient, UrlManifest
from ajp.connectors.jobposting import _strip_html, classify_location
from ajp.extract.text import decode_bytes, decode_entities
from ajp.extract_contacts import extract_contacts
from ajp.extract import rs_text
from ajp.extract.intl_text import kind_row_fields
from ajp.rawstore import put_raw
from ajp.robots import RobotsDenied
from selectolax.parser import HTMLParser

_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TITLE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S)
_LINK = re.compile(r"<link>(.*?)</link>", re.S)
_PUBDATE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
_DESCRIPTION = re.compile(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", re.S)
_CONTENT_ENCODED = re.compile(r"<content:encoded>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</content:encoded>", re.S)

DEFAULT_FEED_PATHS = ("/feed/", "/?feed=rss2")
DEFAULT_SITEMAP_PATH = "/wp-sitemap.xml"

# packet 009's own EN keyword set for the classifier gate; Serbian boards reuse rs_text.academic_ad instead.
_EN_ACADEMIC_KEYWORDS = re.compile(
    r"\bphd\b|\bpostdoc\w*\b|\bresearch (fellow|associate)\b|\bscientist\b|\blecturer\b|\bprofessor\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class WordPressBoardConfig:
    source_id: str
    host: str                                          # e.g. "sljaka.com" (no scheme, no trailing slash)
    org_raw: Optional[str] = None
    feed_paths: Tuple[str, ...] = DEFAULT_FEED_PATHS
    category_feed_paths: Tuple[str, ...] = ()          # e.g. ("/category/poslovi/feed/",)
    classifier: str = "intl"                           # 'intl' | 'rs' - which text classifier gates/tags rows
    # A single-country board's own known country (e.g. 'RS' for a Serbian board, 'CH' for a Swiss one) - a
    # FACT about the organisation being configured, supplied by whoever writes the config, exactly like
    # org_raw; this generic family has no structured per-row location field to read one from (unlike
    # jobposting.py's schema.org path or cern.py's own bespoke '.location' selector), so without this a row
    # would otherwise need a guessed country, which D6 forbids - it stays unresolved (country_iso2=None, with
    # a real location_class in its place - T23, PM ruling R2) instead.
    fixed_country: Optional[str] = None
    detail_selectors: Tuple[str, ...] = ("article", "main", ".entry-content", "body")
    # For a feed that is already category-scoped to a single kind (fondzanauku.gov.rs's own '/category/calls/'
    # feed, PM-verified 2026-09-23: every item is a funding call, and titles like 'Public Call for the Joint
    # Research Program' never match the employment keyword gate at all, which would silently drop every row).
    # Setting this both skips the title pre-filter (the feed's own category already did that filtering) and
    # fixes every row's kind - a real fact about this config, supplied by whoever wrote it after confirming
    # the category's own scope, never a guess about any single ad's own text.
    default_kind: Optional[str] = None


def _looks_like_feed(body: bytes) -> bool:
    head = body[:600].lower()
    return b"<rss" in head or b"<feed" in head or b"<?xml" in head


def candidate_feed_urls(config: WordPressBoardConfig) -> List[str]:
    """Pure, no I/O - the ordered list of feed URLs discover_feed() will try. Exposed separately so a test
    (or a caller wanting to log the whole candidate list) doesn't need a live/fake client just to see it."""
    return [f"https://{config.host}{p}" for p in (*config.feed_paths, *config.category_feed_paths)]


def parse_feed_items(xml_text: str) -> List[Dict[str, str]]:
    items = []
    for block in _ITEM.findall(xml_text):
        title_m, link_m = _TITLE.search(block), _LINK.search(block)
        if not (title_m and link_m):
            continue
        pub_m, desc_m, content_m = _PUBDATE.search(block), _DESCRIPTION.search(block), _CONTENT_ENCODED.search(block)
        # T29 (packet 017, PM-verified live): this regex-parses raw RSS XML directly (never a real XML/HTML
        # parser, unlike board.py's own selectolax-based card parsing) - a real feed item's own title/
        # description/content:encoded can carry '&amp;'/'&quot;' exactly as higher_ed_jobsrss.py's identical
        # regex shape did before that fix; decoded once, right after each field is captured.
        items.append({
            "title": decode_entities(title_m.group(1).strip()), "link": decode_entities(link_m.group(1).strip()),
            "pub_date": pub_m.group(1).strip() if pub_m else "",
            "description": decode_entities(desc_m.group(1).strip()) if desc_m else "",
            "content": decode_entities(content_m.group(1).strip()) if content_m else "",
        })
    return items


def _parse_rfc822(pub_date: Optional[str]) -> Optional[datetime]:
    """RSS <pubDate> is RFC 822 ('Fri, 11 Sep 2026 12:38:44 +0000'), not ISO 8601 - jobposting._parse_dt only
    handles the latter (that module's sources are all schema.org JSON-LD). sljaka.py already established
    email.utils.parsedate_to_datetime as this project's own RFC822 parser; reused here rather than
    reinvented (S2, PM code review 2026-09-23: this field was being discarded)."""
    if not pub_date:
        return None
    try:
        dt = parsedate_to_datetime(pub_date)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _classifier_gate(title: str, classifier: str) -> bool:
    """A cheap, title-only pre-filter BEFORE fetching any detail page (packet 009: 'only subscribe to
    career-relevant categories') - a full kind/position classification still runs on title+body afterward;
    this only decides whether a feed item is worth a detail fetch at all."""
    if classifier == "rs":
        return rs_text.academic_ad(title)
    return bool(_EN_ACADEMIC_KEYWORDS.search(title))


class WordPressConnector(Connector):
    kind = "feed"

    def __init__(self, client: PoliteClient, store: ObjectStore, config: WordPressBoardConfig,
                org_id: Optional[str] = None):
        self.config = config
        self.source_id = config.source_id
        self.org_id = org_id
        all_paths = (*config.feed_paths, *config.category_feed_paths, DEFAULT_SITEMAP_PATH)
        self.manifest = UrlManifest(
            *(re.escape(f"https://{config.host}{p}") for p in all_paths),
            # detail pages under this same host - http:// too: fondzanauku.gov.rs's own <link> elements use
            # plain http (found live, 2026-09-23) despite the site otherwise being reachable over https;
            # PoliteClient still robots-gates and rate-limits either scheme identically, so permitting both
            # only widens which of the org's own published links this connector can actually follow. The
            # path allows a query string too ('[^\s#]*', not '[^\s?#]+/?') - the original pattern excluded
            # '?' entirely, so fondzanauku's own '?lang=en'-suffixed links (also found live) never matched
            # at all, regardless of scheme.
            rf"https?://{re.escape(config.host)}/[^\s#]*",
        )
        super().__init__(client, store)

    @staticmethod
    def ensure_source(conn, config: WordPressBoardConfig, feed_url: str) -> str:
        # ats_family='gen_wordpress' - without this, route_type_label() (T18 registry) can't tell this
        # source apart from an unclassified 'channel_or_other' one; found live 2026-09-23 re-checking this
        # module's own registration path, not hypothesised. geo_scope follows the config's own fixed_country
        # when the config states one (a real fact about the org, same as fixed_country itself), else falls
        # back to 'RS'/'INTL' by which classifier the config uses - never guessed beyond that.
        geo_scope = config.fixed_country or ("RS" if config.classifier == "rs" else "INTL")
        # T21: the scheduler's own generic factory (ajp.scheduler.build_connector) reconstructs a
        # WordPressBoardConfig straight from this column - found live (the first real ajp-run against a
        # real seed) that this write was missing entirely, so every gen_wordpress source's own config
        # silently stayed '{}' forever and the factory crashed on the very first real run. dataclasses.
        # asdict() round-trips every field (its own Tuple[str,...] fields serialise as plain JSON arrays;
        # ajp.scheduler's own _coerce_config_fields() converts them back on the way out).
        conn.execute(
            """INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope,
                                   ats_family, tdm_reservation, config, notes)
               VALUES (%s,'feed',%s,%s,true,1.0,%s,'gen_wordpress','none',%s,
                       'gen_wordpress generic family - discovered feed path, robots-gated per path')
               ON CONFLICT (id) DO UPDATE SET config = EXCLUDED.config""",
            (config.source_id, f"https://{config.host}/", feed_url, geo_scope,
             Jsonb(asdict(config))),
        )
        return config.source_id

    def discover_feed(self) -> Optional[str]:
        """The first candidate feed path that is robots-allowed AND actually returns something feed-shaped -
        never assumed just because a path is conventional (packet 009's own infostud counter-example: a
        WP-like board can still robots-disallow every feed path). Goes through self.fetch_raw() like every
        other request this connector makes - the same governance path, and testable via the same Offline-
        connector-subclass pattern every other family in this project uses (found live building this: a
        module-level function taking a bare client bypassed that pattern entirely)."""
        for url in candidate_feed_urls(self.config):
            try:
                res, _ = self.fetch_raw(url, kind="xml")
            except (FetchFailed, ManifestViolation, RobotsDenied):
                continue
            if res.status == 200 and _looks_like_feed(res.content):
                return url
        return None

    def discover(self) -> List[Dict[str, str]]:
        feed_url = self.discover_feed()
        if feed_url is None:
            return []
        res, _ = self.fetch_raw(feed_url, kind="xml")
        items = parse_feed_items(decode_bytes(res.content, res.content_type))
        if self.config.default_kind is not None:
            return items                                       # the feed's own category already scoped this
        return [it for it in items if _classifier_gate(it["title"], self.config.classifier)]

    def crawl(self, items: Optional[List[Dict[str, str]]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        items = items if items is not None else self.discover()
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(items), "parsed": 0, "kept": 0, "discarded": 0, "no_country": 0, "no_body_or_ref": 0}
        for item in items:
            body_text, raw_ref, detail_html = "", None, ""
            try:
                res, ref = self.fetch_raw(item["link"])
                if res.status == 200:
                    detail_html = decode_bytes(res.content, res.content_type)
                    body_text = self._extract_body(detail_html)
                    raw_ref = ref.key
            except (FetchFailed, ManifestViolation, RobotsDenied):
                pass                                             # detail page unusable - the feed fallback below may still work
            # the feed's own <description>/<content:encoded> is often empty on a custom job-board theme
            # (careers.cern, live-verified) but FULL on others (fondzanauku, KU DK-style boards) - used
            # whenever the detail page gave nothing at all, INCLUDING when the detail fetch itself failed
            # (a 404/robots-denied detail link must never discard a row the feed alone already fully supports).
            if not body_text:
                fallback_html = item.get("content") or item.get("description") or ""
                fallback = _strip_html(fallback_html) or ""
                if fallback:
                    body_text = fallback
                    detail_html = detail_html or fallback_html   # T26: mailto:/tel: links may live in the
                                                                  # feed's own <content:encoded> markup too
                    if raw_ref is None:
                        # no usable detail-page envelope to cite as provenance - package the feed item's own
                        # content as this row's raw object instead. No new HTTP request: these bytes were
                        # already fetched (and robots-cleared) as part of the feed fetch itself; status=200
                        # accurately reflects that this data really was obtained, just not via its own request.
                        raw_ref = put_raw(self.store, kind="html", source_id=self.source_id, url=item["link"],
                                          content=fallback.encode("utf-8"), fetched_at=datetime.now(timezone.utc),
                                          status=200, content_type="text/html", robots_allowed=True).key
            if raw_ref is None:
                report["no_body_or_ref"] += 1
                continue                                          # neither a usable detail page nor usable feed content
            row = self._map_row(item, body_text, raw_ref, html=detail_html)
            report["parsed"] += 1
            if row is None:
                report["no_country"] += 1
                continue
            rows.append(row)
            report["kept" if row["kind"] == "employment" else "discarded"] += 1
        return rows, report

    def _extract_body(self, html: str) -> str:
        tree = HTMLParser(html)
        for tag in tree.css("script, style"):
            tag.decompose()
        for selector in self.config.detail_selectors:
            node = tree.css_first(selector)
            if node is not None:
                text = re.sub(r"\s+", " ", node.text(separator=" ")).strip()
                if text:
                    return text
        return ""

    def _map_row(self, item: Dict[str, str], body_text: str, raw_ref: str, html: str = "") -> Optional[Dict[str, Any]]:
        title = item["title"]
        # T48 (packet 028): one call returns the kind AND its {conf, raw} evidence (D6 - a classification
        # may never be persisted without the excerpt that produced it). rs_research_title comes out of the
        # same dict and is popped below; it is a separate column, not evidence.
        if self.config.classifier == "rs":
            kind_f = rs_text.kind_row_fields(f"{title} {body_text}", title)
        else:
            kind_f = kind_row_fields(title, body_text)
        rs_research_title = kind_f.pop("rs_research_title", None)
        if self.config.default_kind is not None:
            # The site's configured default_kind OVERRIDES the classifier's own answer, so from here on the
            # classifier's evidence no longer describes the kind being persisted. Keep the override (it is
            # the source's own configured truth) but DROP the evidence rather than attach an excerpt to a
            # value it did not produce - a 'praksa' span next to kind='employment' would be a fabricated
            # provenance, which is exactly what D6 forbids.
            if self.config.default_kind != kind_f["kind"]:
                kind_f = {**kind_f, "kind": self.config.default_kind, "kind_conf": None, "kind_raw": None}
        kind, position_kind = kind_f["kind"], kind_f["position_kind"]
        # T23 (PM ruling R2): a missing fixed_country is still never guessed from free text (D6 unchanged) -
        # but the row is persisted with country_iso2=None + a real location_class now (migration 0020),
        # rather than discarded outright.
        country = self.config.fixed_country
        location = classify_location(f"{title} {body_text}", has_country=country is not None)
        contacts = extract_contacts(html=html, text=body_text)
        row: Dict[str, Any] = {
            "ext_uid": hashlib.sha1(item["link"].encode()).hexdigest()[:16], "source_id": self.source_id,
            "url": item["link"], "title_raw": title, "org_id": self.org_id, "org_raw": self.config.org_raw,
            "country_iso2": country, **kind_f,
            "ft_pt": "unknown", "raw_text": body_text[:4000], "requirements_raw": body_text[:4000],
            "foreigner_eligible": "unknown", "extraction_family": "gen_wordpress", "raw_html_ref": raw_ref,
            "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
            "location_class_raw": location["location_class_raw"],
            "apply_url": item["link"], **contacts,
        }
        if rs_research_title:
            row["rs_research_title"] = rs_research_title
        posted = _parse_rfc822(item.get("pub_date"))
        if posted:
            row["posted_at"] = posted
        return row
