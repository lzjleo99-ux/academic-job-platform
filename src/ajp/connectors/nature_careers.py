"""Nature careers (nature.com/naturecareers), T13 wave A. Sitemap-driven, schema.org JobPosting detail pages
(ajp.connectors.jobposting). robots.txt (verified 2026-09-22) disallows the site's own faceted listing-search
page (plural 'jobs', a query-based results page) and the generic site-wide search - neither is ever fetched;
the sitemap is the only discovery route, and the singular '/job/{id}/' detail pattern this module matches
cannot overlap with either disallowed page's own URL shape. The sitemap index lists MULTIPLE shard families
(sitemap1-10x.xml = faceted SEO landing pages
for category/country combinations, NOT job postings; sitemap3/4-1.xml = employer pages; sitemap5.xml = category
landing pages; sitemap7.xml = editorial articles) - live-verified 2026-09-22 that only the 'sitemap2-*.xml'
family holds actual job postings (singular '/naturecareers/job/{id}/{slug}/', matched via schema.org
JobPosting JSON-LD; a depth3 URL under the plural 'jobs' category/country facet path, despite looking like a
detail page, is a landing page with NO JSON-LD - this is a correction to an earlier assumption that depth3 URLs
were job details, found and reported before building this connector). Shard names/count are re-discovered from
the index every run, never hardcoded (PM's own instruction, and matches the live evidence that shard suffixes
already vary: 1-100..1-104, 2-1, 3-1, 4-1, 5, 7).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, UrlManifest
from ajp.connectors.jobposting import extract_jobposting, fetch_sitemap_text, jobposting_to_row, sitemap_locs
from ajp.extract.text import decode_bytes
from ajp.robots import RobotsDenied

HOST = "www.nature.com"
SITEMAPINDEX_URL = f"https://{HOST}/naturecareers/sitemapindex.xml"
MANIFEST = UrlManifest(
    rf"https://{HOST}/naturecareers/sitemapindex\.xml",
    rf"https://{HOST}/naturecareers/sitemap[\w-]+\.xml",
    rf"https://{HOST}/naturecareers/job/\d+/[^?#\s]+/?",
)

# robots.txt disallows the site's plural listing-search page and the generic site-wide search (never fetched) -
# both are already structurally excluded by this pattern requiring the SINGULAR 'job' plus a numeric id, so
# there is no separate disallow-list to maintain (and no risk of drifting out of sync with it).
_JOB_DETAIL = re.compile(r"^https://[^/]+/naturecareers/job/\d+/")


def is_job_detail_url(url: str) -> bool:
    return bool(_JOB_DETAIL.match(url))


class NatureCareersConnector(Connector):
    source_id = "nature_careers"
    kind = "scrape_html"
    manifest = MANIFEST

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('nature_careers','scrape_html',%s,%s,true,1.0,'INTL',
                                'Tier-1 global academic aggregator (Springer Nature); schema.org JobPosting '
                                'JSON-LD detail pages; sitemap-only discovery, never the disallowed listing- or '
                                'site-search pages - only the sitemap2-*.xml shard family holds '
                                'actual job postings, the rest are landing/employer/article pages')
                        ON CONFLICT (id) DO NOTHING""", (f"https://{HOST}/naturecareers/", SITEMAPINDEX_URL))

    def discover(self) -> List[str]:
        """sitemapindex.xml -> every shard -> every real job detail URL in those shards. Shards that turn out
        to hold zero job-detail URLs (landing/employer/article families) are simply skipped, never assumed
        from the shard's own name - the index's shard list itself is never hardcoded either."""
        shard_urls = [u for u in sitemap_locs(fetch_sitemap_text(self, SITEMAPINDEX_URL), HOST) if u.endswith(".xml")]
        urls: List[str] = []
        for shard in shard_urls:
            urls.extend(u for u in sitemap_locs(fetch_sitemap_text(self, shard), HOST) if is_job_detail_url(u))
        return urls

    def crawl(self, urls: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(urls), "parsed": 0, "no_jobposting": 0, "kept": 0, "discarded": 0, "fetch_errors": 0}
        for url in urls:
            # F7 (PM live audit 2026-09-24): the real bug reported - one detail URL (nature.com job 12864447)
            # returning a 502 after retries aborted the ENTIRE source, losing every sibling row already
            # collected. Caught and counted per item instead, never propagated.
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
