"""AcademicTransfer (academictransfer.com), T13 wave A. Sitemap-driven, schema.org JobPosting detail pages
(see ajp.connectors.jobposting for the shared extractor). robots.txt (verified 2026-09-22): the site has
explicit per-bot rules for named AI crawlers (ClaudeBot/GPTBot/Google-Extended/CCBot/... all Disallow: /,
framed as 'AI training crawlers may NOT collect data for model training'), but 'User-agent: *' - which is what
this project's own, honestly-identified User-Agent falls under, never impersonating any named bot - states
'Allow: /' with 'Crawl-delay: 10'; only /account/ and /apply/ are disallowed for '*'. PoliteClient's own
Crawl-delay support (built for this site) applies automatically; this module adds no extra throttling.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, UrlManifest
from ajp.connectors.jobposting import extract_jobposting, jobposting_to_row, sitemap_locs
from ajp.extract.text import decode_bytes
from ajp.robots import RobotsDenied

HOST = "www.academictransfer.com"
SITEMAP_URL = f"https://{HOST}/sitemap-vacancies.xml"
MANIFEST = UrlManifest(
    rf"https://{HOST}/sitemap-vacancies\.xml",
    rf"https://{HOST}/(?:en|nl)/jobs/\d+/[^?#\s]+/?",
)


def is_vacancy_url(url: str) -> bool:
    return "/jobs/" in url and "/account/" not in url and "/apply/" not in url


class AcademicTransferConnector(Connector):
    source_id = "academictransfer"
    kind = "scrape_html"
    manifest = MANIFEST

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('academictransfer','scrape_html',%s,%s,true,0.1,'INTL',
                                'Tier-1 international academic aggregator (NL-based, EU-wide postings); schema.org '
                                'JobPosting JSON-LD detail pages; robots Crawl-delay:10 enforced by PoliteClient')
                        ON CONFLICT (id) DO NOTHING""", (f"https://{HOST}/", SITEMAP_URL))

    def discover(self) -> List[str]:
        """Every vacancy URL in the sitemap, canonicalized to /en/ when both a /nl/ and /en/ variant share the
        same numeric id (the sitemap lists each job under ONE language only in practice, but this stays
        defensive rather than assume that always holds)."""
        res, _ = self.fetch_raw(SITEMAP_URL, kind="xml")
        locs = [u for u in sitemap_locs(decode_bytes(res.content, res.content_type), HOST) if is_vacancy_url(u)]
        by_id: Dict[str, str] = {}
        for u in locs:
            job_id = u.rstrip("/").split("/jobs/", 1)[-1].split("/", 1)[0]
            if job_id not in by_id or "/en/jobs/" in u:
                by_id[job_id] = u
        return list(by_id.values())

    def crawl(self, urls: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Fetch and parse exactly the given URLs (the caller decides how many - a full sweep at Crawl-delay:10
        is ~110 minutes for all ~671 vacancies, fine for a scheduled run, not for an interactive one)."""
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(urls), "parsed": 0, "no_jobposting": 0, "kept": 0, "discarded": 0, "fetch_errors": 0}
        for url in urls:
            # F7 (PM live audit 2026-09-24): one detail URL's fetch failing after retries (e.g. a real 502)
            # must never abort the whole sweep and lose every sibling row already collected - caught and
            # counted per item instead, exactly like kth.py/gen_wordpress.py already do.
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
            row["ad_lang"] = "nl" if "/nl/jobs/" in url else "en"
            rows.append(row)
            if row["kind"] in ("employment", "grant_call", "internship", "enrollment"):
                report["kept"] += 1
            else:
                report["discarded"] += 1
        # If EVERY attempted item failed to even fetch, this is a real outage, not a legitimately empty run -
        # raise so the scheduler's own R1 3-strikes counter and ledger status='error'/'blocked' still see it
        # (never silently reported as a clean, empty 'ok' run).
        if report["attempted"] > 0 and report["fetch_errors"] == report["attempted"]:
            raise FetchFailed(f"all {report['attempted']} detail fetches failed for {self.source_id}")
        return rows, report
