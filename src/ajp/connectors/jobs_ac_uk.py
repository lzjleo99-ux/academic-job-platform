"""jobs.ac.uk, T13 wave A. Sitemap-driven (sitemapindex.xml -> S3-hosted shard(s), served with content-type
application/octet-stream - parsed as XML regardless, see jobposting.sitemap_locs), schema.org JobPosting detail
pages (ajp.connectors.jobposting). robots.txt (verified 2026-09-22): /search/ allowed, only /job/feedback/ and
/enhanced/fp/ disallowed under /job/ - the sitemap is the primary discovery route, HTML search is reserved for
a later keyword-backfill pass, never built here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, UrlManifest
from ajp.connectors.jobposting import extract_jobposting, fetch_sitemap_text, jobposting_to_row, sitemap_locs
from ajp.extract.text import decode_bytes
from ajp.robots import RobotsDenied

HOST = "www.jobs.ac.uk"
SITEMAPINDEX_URL = f"https://{HOST}/sitemapindex.xml"
MANIFEST = UrlManifest(
    rf"https://{HOST}/sitemapindex\.xml",
    rf"https://{HOST}/sitemap\d+\.xml",
    # (?!feedback/) excludes /job/feedback/{id} - it otherwise fits the same {CODE}/{slug} shape as a real
    # detail page (found by the T13 denied-route guard test: 'feedback' itself is a valid-looking CODE token).
    rf"https://{HOST}/job/(?!feedback/)[A-Za-z0-9]+/[^?#\s]+/?",
    # sitemapindex.xml redirects to an S3-hosted copy (live, 2026-09-22) - the redirect hop itself must be in
    # the manifest too (PoliteClient re-checks it for every hop, R2/R3's own convention: a render/redirect
    # target is never worked around, only explicitly permitted once verified).
    r"https://s3[-.][a-z0-9-]+\.amazonaws\.com/www\.jobs\.ac\.uk/sitemap(?:index)?\d*\.xml",
)
_DISALLOWED_JOB_PATHS = ("/job/feedback/", "/enhanced/fp/")


def is_job_detail_url(url: str) -> bool:
    if any(p in url for p in _DISALLOWED_JOB_PATHS):
        return False
    path = url.split(HOST, 1)[-1] if HOST in url else url
    return path.startswith("/job/") and path.count("/") >= 3


class JobsAcUkConnector(Connector):
    source_id = "jobs_ac_uk"
    kind = "scrape_html"
    manifest = MANIFEST

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('jobs_ac_uk','scrape_html',%s,%s,true,1.0,'INTL',
                                'Tier-1 UK-based academic aggregator (global postings observed too); schema.org '
                                'JobPosting JSON-LD detail pages; sitemap-only discovery, never /search')
                        ON CONFLICT (id) DO NOTHING""", (f"https://{HOST}/", SITEMAPINDEX_URL))

    def discover(self) -> List[str]:
        """sitemapindex.xml -> every shard it lists -> every /job/{CODE}/{slug} url in those shards. The shard
        list is discovered fresh each run (PM: 'may change - discover from the index each run, never hardcode')."""
        shard_urls = [u for u in sitemap_locs(fetch_sitemap_text(self, SITEMAPINDEX_URL), HOST) if u.endswith(".xml")]
        urls: List[str] = []
        for shard in shard_urls:
            urls.extend(u for u in sitemap_locs(fetch_sitemap_text(self, shard), HOST) if is_job_detail_url(u))
        return urls

    def crawl(self, urls: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(urls), "parsed": 0, "no_jobposting": 0, "kept": 0, "discarded": 0, "fetch_errors": 0}
        for url in urls:
            # F7 (PM live audit 2026-09-24): a single detail URL's fetch failing after retries must never
            # abort the whole sweep and lose every sibling row already collected - caught and counted per
            # item instead.
            try:
                res, ref = self.fetch_raw(url)
            except (FetchFailed, ManifestViolation, RobotsDenied):
                report["fetch_errors"] += 1
                continue
            if res.status != 200:
                continue
            html = decode_bytes(res.content, res.content_type)
            jp = extract_jobposting(html)
            if jp is None:
                report["no_jobposting"] += 1
                continue
            report["parsed"] += 1
            row = jobposting_to_row(jp, res.final_url, self.source_id, ref.key, html=html)
            if row is None:
                continue
            row["ad_lang"] = "en"
            rows.append(row)
            if row["kind"] in ("employment", "grant_call", "internship", "enrollment"):
                report["kept"] += 1
            else:
                report["discarded"] += 1
        if report["attempted"] > 0 and report["fetch_errors"] == report["attempted"]:
            raise FetchFailed(f"all {report['attempted']} detail fetches failed for {self.source_id}")
        return rows, report
