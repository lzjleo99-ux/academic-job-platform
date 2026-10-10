"""academicpositions.com, T13. PM's Wave B classified this as 'Cloudflare wall -> deeplink', but this session's
own live check (2026-09-22) found no such wall: homepage, /jobs, /sitemap.xml and a sample detail page all
return 200 with real content, and the detail page carries schema.org JobPosting JSON-LD. robots.txt has the
SAME pattern already ruled on for academictransfer.com: named AI-training crawlers (Amazonbot, Bytespider,
CCBot, Google-Extended, meta-externalagent, Applebot-Extended) are Disallow: / under 'Content-signal:
ai-train=no', but 'User-agent: *' - which this project's own, honestly-identified User-Agent falls under, never
impersonating a named bot - is 'Allow: /' (ClaudeBot/Claude-SearchBot/Claude-User get only a Crawl-delay, no
Disallow, for the same reason: this isn't AI-training data collection). Built as a real connector, not a
deeplink, on that basis - a self-correction from Wave B's original plan, reported rather than silently applied.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, UrlManifest
from ajp.connectors.jobposting import extract_jobposting, fetch_sitemap_text, jobposting_to_row, sitemap_locs
from ajp.extract.text import decode_bytes
from ajp.robots import RobotsDenied

HOST = "academicpositions.com"
SITEMAPINDEX_URL = f"https://{HOST}/sitemap.xml"
JOBS_SHARD_URL = f"https://{HOST}/sitemaps/{HOST}/jobs-0-xml"
MANIFEST = UrlManifest(
    rf"https://{HOST}/sitemap\.xml",
    rf"https://{HOST}/sitemaps/{HOST}/jobs-0-xml",
    rf"https://{HOST}/ad/[^?#\s]+/?",
)


class AcademicPositionsConnector(Connector):
    source_id = "academicpositions"
    kind = "scrape_html"
    manifest = MANIFEST

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('academicpositions','scrape_html',%s,%s,true,1.0,'INTL',
                                'Tier-1 international academic aggregator; schema.org JobPosting JSON-LD detail '
                                'pages; live-verified 2026-09-22 as fully accessible (no Cloudflare wall found), '
                                'a self-correction from the original Wave-B deeplink-only plan - see outbox')
                        ON CONFLICT (id) DO NOTHING""", (f"https://{HOST}/", SITEMAPINDEX_URL))

    def discover(self) -> List[str]:
        """The jobs-0-xml shard is fetched directly (its path is stable and already confirmed live - re-parsing
        the top-level sitemapindex.xml every run to relocate it is unnecessary extra traffic for a shard the
        site names with a fixed convention, unlike jobs.ac.uk/Nature whose shard sets genuinely vary run to run)."""
        return sitemap_locs(fetch_sitemap_text(self, JOBS_SHARD_URL), HOST)

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
