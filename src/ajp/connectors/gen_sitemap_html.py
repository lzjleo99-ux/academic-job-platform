"""T18 gen_sitemap_html: a generic, config-driven connector for any sitemap-discovered job board - one engine,
many sites (packet 009's own architecture_ruling), covering both boards that publish real schema.org
JobPosting JSON-LD on their detail pages (reuse T13's own extractor, jobposting.py) and boards that don't
(a generic classifier + CSS-selector body extraction, the same shape board.py/gen_wordpress.py already use).

Discovery: sitemapindex -> shards (any URL matching ``shard_url_pattern``, including a gzip-compressed
``.xml.gz`` shard - a real, physically-gzipped file, not merely an HTTP Content-Encoding, so it needs an
explicit decompress) -> individual job-detail URLs (any shard <loc> matching ``detail_url_pattern`` - a
sitemap ordinarily also lists browse/facet/employer/article pages that are never job postings themselves).
``lastmod`` from the shard is kept on each discovered URL so a future incremental run can skip anything not
newer than its last successful crawl (T19/T20's own job, not built into this module directly).

Real live-verified example this engine's shape is drawn from: timeshighereducation.com/unijobs (THEunijobs) -
sitemapindex -> sitemap2-1.xml (1134 real job-detail URLs, schema.org JobPosting JSON-LD, PM-verified live
2026-09-23) - the live-registered seed for this family (see tools/dev_up.py). CNRS's own sitemap-driven board
(emploi.cnrs.fr, T13) already has its own bespoke, purpose-built connector (cnrs.py) with a real classifier
for a site that does NOT publish JobPosting JSON-LD - used here only as a validation FIXTURE proving this
engine's 'generic_html' detail mode reproduces that real, live-verified shape, never re-registered as a
second, duplicate-ingesting source for the same host.
"""
from __future__ import annotations

import gzip
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from psycopg.types.json import Jsonb

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, ObjectStore, PoliteClient, UrlManifest
from ajp.connectors.jobposting import _strip_html, classify_location, extract_jobposting, jobposting_to_row, sitemap_locs
from ajp.extract_contacts import extract_contacts
from ajp.robots import RobotsDenied
from ajp.extract import rs_text
from ajp.extract.intl_text import classify_kind_intl, position_kind_intl
from ajp.extract.text import decode_bytes, decode_entities
from selectolax.parser import HTMLParser

DEFAULT_SHARD_PATTERN = r"\.xml(\.gz)?(\?.*)?$"


@dataclass(frozen=True)
class SitemapBoardConfig:
    source_id: str
    host: str
    sitemapindex_url: str
    detail_url_pattern: str                            # e.g. r"/unijobs/listing/\d+/"
    shard_url_pattern: str = DEFAULT_SHARD_PATTERN
    detail_mode: str = "jsonld"                         # 'jsonld' | 'generic_html'
    classifier: str = "intl"                            # generic_html mode only
    fixed_country: Optional[str] = None                 # generic_html mode only (jsonld mode reads its own)
    org_raw: Optional[str] = None
    detail_selectors: Tuple[str, ...] = ("article", "main", ".entry-content", "body")


def _maybe_gunzip(url: str, content: bytes) -> bytes:
    if url.endswith(".gz"):
        try:
            return gzip.decompress(content)
        except OSError:
            return content                                # not actually gzip despite the extension - pass through
    return content


class SitemapHtmlConnector(Connector):
    kind = "scrape_html"

    def __init__(self, client: PoliteClient, store: ObjectStore, config: SitemapBoardConfig,
                org_id: Optional[str] = None):
        self.config = config
        self.source_id = config.source_id
        self.org_id = org_id
        self.manifest = UrlManifest(
            re.escape(config.sitemapindex_url),
            rf"https?://{re.escape(config.host)}/[^\s#]*\.xml(\.gz)?(\?[^\s#]*)?",   # any shard on this host
            rf"https?://{re.escape(config.host)}/[^\s#]*",                          # any detail page on this host
        )
        super().__init__(client, store)

    @staticmethod
    def ensure_source(conn, config: SitemapBoardConfig) -> str:
        # ats_family='gen_sitemap_html' - see gen_wordpress.ensure_source's own note (T18 registry, live
        # 2026-09-23): without this the row is unclassified in route_type_label(). geo_scope follows the
        # config's own fixed_country (generic_html mode) or classifier-implied default; a jsonld-mode config
        # (THEunijobs) reads each row's own country from schema.org data and stays 'INTL' here - a real,
        # multi-country aggregator, not a single-country fact to state at the source level.
        geo_scope = config.fixed_country or ("RS" if config.classifier == "rs" else "INTL")
        # T21: ajp.scheduler.build_connector reconstructs a SitemapBoardConfig straight from this column -
        # found live (the first real ajp-run) that this write was missing entirely for gen_wordpress, the
        # same bug this family would have had too; fixed here alongside it before it was ever hit live.
        conn.execute(
            """INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope,
                                   ats_family, tdm_reservation, config, notes)
               VALUES (%s,'scrape_html',%s,%s,true,1.0,%s,'gen_sitemap_html','none',%s,
                       'gen_sitemap_html generic family - sitemapindex-discovered detail URLs, robots-gated')
               ON CONFLICT (id) DO UPDATE SET config = EXCLUDED.config""",
            (config.source_id, f"https://{config.host}/", config.sitemapindex_url, geo_scope, Jsonb(asdict(config))),
        )
        return config.source_id

    def discover(self) -> List[str]:
        # F9 (PM live audit 2026-09-24): a non-200 index/shard fetch (a challenge/maintenance page, a 404 -
        # anything but real sitemap XML) must never be silently gunzip-decoded and regex-scanned as if it
        # were one - that just matches zero <loc> tags and reports a clean, empty 'nothing new today' run
        # forever, hiding a real, ongoing failure. Raise instead so the scheduler's own exception-isolation +
        # R1 3-strikes counter see it.
        idx_res, _ = self.fetch_raw(self.config.sitemapindex_url, kind="xml")
        if idx_res.status != 200:
            raise FetchFailed(f"non-200 status {idx_res.status} fetching sitemapindex {self.config.sitemapindex_url}")
        idx_body = _maybe_gunzip(self.config.sitemapindex_url, idx_res.content)
        shard_urls = [u for u in sitemap_locs(decode_bytes(idx_body, idx_res.content_type), self.config.host)
                     if re.search(self.config.shard_url_pattern, u)]
        detail_urls: List[str] = []
        for shard in shard_urls:
            res, _ = self.fetch_raw(shard, kind="xml")
            if res.status != 200:
                raise FetchFailed(f"non-200 status {res.status} fetching sitemap shard {shard}")
            body = _maybe_gunzip(shard, res.content)
            detail_urls.extend(u for u in sitemap_locs(decode_bytes(body, res.content_type), self.config.host)
                               if re.search(self.config.detail_url_pattern, u))
        return detail_urls

    def crawl(self, urls: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        urls = urls if urls is not None else self.discover()
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(urls), "parsed": 0, "kept": 0, "discarded": 0, "no_country": 0, "fetch_errors": 0}
        for url in urls:
            # F7 (PM live audit 2026-09-24): a single detail URL's fetch failing after retries must never
            # abort the whole sweep and lose every sibling row already collected.
            try:
                res, ref = self.fetch_raw(url)
            except (FetchFailed, ManifestViolation, RobotsDenied):
                report["fetch_errors"] += 1
                continue
            if res.status != 200:
                continue
            html = decode_bytes(res.content, res.content_type)
            row = (self._map_jsonld_row(html, res.final_url, ref.key) if self.config.detail_mode == "jsonld"
                  else self._map_generic_html_row(html, url, ref.key))
            report["parsed"] += 1
            if row is None:
                report["no_country"] += 1
                continue
            if self.org_id and not row.get("org_id"):
                row["org_id"] = self.org_id
            rows.append(row)
            report["kept" if row["kind"] == "employment" else "discarded"] += 1
        if report["attempted"] > 0 and report["fetch_errors"] == report["attempted"]:
            raise FetchFailed(f"all {report['attempted']} detail fetches failed for {self.source_id}")
        return rows, report

    def _map_jsonld_row(self, html: str, url: str, raw_ref: str) -> Optional[Dict[str, Any]]:
        jp = extract_jobposting(html)
        if jp is None:
            return None
        row = jobposting_to_row(jp, url, self.source_id, raw_ref, html=html)
        if row is not None:
            row["ad_lang"] = "en"
        return row

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

    def _map_generic_html_row(self, html: str, url: str, raw_ref: str) -> Optional[Dict[str, Any]]:
        # T23 (PM ruling R2): no structured location field in this mode is no longer a discard reason - the
        # row is persisted with country_iso2=None + a real location_class instead (migration 0020). D6
        # unchanged: fixed_country is a real, config-declared fact about the org's own site, never guessed;
        # when it's absent, the country is simply left null rather than fabricated.
        country = self.config.fixed_country
        tree = HTMLParser(html)
        title_el = tree.css_first("h1") or tree.css_first("title")
        # T29 (packet 017): decoded defensively - selectolax's own .text() already resolves a normal,
        # single-escaped entity correctly, but a site whose OWN raw markup is double-escaped
        # ('&amp;quot;') only gets ONE level undone by any correct parser, leaving a literal '&quot;' as
        # real text content (found live in board.py's own document-title path against a real RS org site).
        # Idempotent no-op on every already-clean title (see ajp.extract.text's own contract).
        title = decode_entities(title_el.text().strip()) if title_el else ""
        if not title:
            return None
        body_text = decode_entities(self._extract_body(html))
        if self.config.classifier == "rs":
            kind = rs_text.classify_kind(f"{title} {body_text}")
            position_kind, rs_research_title = rs_text.position_kind(title)
        else:
            kind = classify_kind_intl(title, body_text)
            position_kind = position_kind_intl(title, body_text)
            rs_research_title = None
        location = classify_location(f"{title} {body_text}", has_country=country is not None)
        contacts = extract_contacts(html=html, text=body_text)
        row: Dict[str, Any] = {
            "ext_uid": hashlib.sha1(url.encode()).hexdigest()[:16], "source_id": self.source_id,
            "url": url, "title_raw": title, "org_raw": self.config.org_raw, "country_iso2": country,
            "position_kind": position_kind, "kind": kind, "ft_pt": "unknown",
            "raw_text": body_text[:4000], "requirements_raw": body_text[:4000],
            "foreigner_eligible": "unknown", "extraction_family": "gen_sitemap_html", "raw_html_ref": raw_ref,
            "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
            "location_class_raw": location["location_class_raw"],
            "apply_url": url, **contacts,
        }
        if rs_research_title:
            row["rs_research_title"] = rs_research_title
        return row
