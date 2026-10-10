from datetime import datetime, timezone

import pytest

from ajp.connectors import nature_careers as nc
from ajp.connectors.base import FetchFailed, FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)

SITEMAPINDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.nature.com/naturecareers/sitemap1-100.xml</loc></sitemap>
<sitemap><loc>https://www.nature.com/naturecareers/sitemap2-1.xml</loc></sitemap>
<sitemap><loc>https://www.nature.com/naturecareers/sitemap7.xml</loc></sitemap>
</sitemapindex>"""

LANDING_SHARD = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.nature.com/naturecareers/jobs/applied-science/bioinformatician/china/</loc></url>
</urlset>"""

JOB_SHARD = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.nature.com/naturecareers/job/12864861/associate-or-senior-editor/</loc></url>
</urlset>"""

ARTICLE_SHARD = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.nature.com/naturecareers/article/how-to-thrive-in-science/</loc></url>
</urlset>"""

DETAIL = """<html><head><script type="application/ld+json">
{"@type":"JobPosting","title":"Associate or Senior Editor, Nature Communications",
 "hiringOrganization":{"name":"Springer Nature Ltd"},
 "jobLocation":[{"address":{"addressCountry":"DE"}},{"address":{"addressCountry":"CN"}}],
 "employmentType":"[\\"FULL_TIME\\"]"}
</script></head><body></body></html>"""


class Offline(nc.NatureCareersConnector):
    pages = {}
    fetched = []
    fail_urls = set()               # F7 regression: simulate a real per-item fetch failure (e.g. a 502)

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        if url in self.fail_urls:
            raise FetchFailed(f"giving up on {url} after 3 attempts: simulated 502")
        body = self.pages[url].encode()
        res = FetchResult(url=url, final_url=url, status=200, content=body, content_type="text/html", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=200, content_type="text/html", robots_allowed=True)
        Offline.fetched.append(url)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


@pytest.mark.parametrize("url,expected", [
    ("https://www.nature.com/naturecareers/job/12864861/associate-or-senior-editor/", True),
    ("https://www.nature.com/naturecareers/jobs/applied-science/bioinformatician/china/", False),  # landing page, not job/
    ("https://www.nature.com/naturecareers/jobs/search", False),
    ("https://www.nature.com/search?q=x", False),
    ("https://www.nature.com/naturecareers/article/how-to-thrive/", False),
])
def test_is_job_detail_url(url, expected):
    assert nc.is_job_detail_url(url) is expected


def test_discover_skips_landing_and_article_shards_keeps_only_real_job_urls(tmp_path):
    Offline.pages = {
        nc.SITEMAPINDEX_URL: SITEMAPINDEX,
        "https://www.nature.com/naturecareers/sitemap1-100.xml": LANDING_SHARD,
        "https://www.nature.com/naturecareers/sitemap2-1.xml": JOB_SHARD,
        "https://www.nature.com/naturecareers/sitemap7.xml": ARTICLE_SHARD,
    }
    c = Offline(fast(), LocalFsStore(tmp_path))
    urls = c.discover()
    assert urls == ["https://www.nature.com/naturecareers/job/12864861/associate-or-senior-editor/"]


def test_crawl_handles_multi_location_jobs_and_json_encoded_employment_type(tmp_path):
    url = "https://www.nature.com/naturecareers/job/12864861/associate-or-senior-editor/"
    Offline.pages = {url: DETAIL}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([url])
    assert report == {"attempted": 1, "parsed": 1, "no_jobposting": 0, "kept": 0, "discarded": 1, "fetch_errors": 0}   # editor role, not an academic title
    assert rows[0]["country_iso2"] == "DE" and rows[0]["ft_pt"] == "full"


def test_never_requests_disallowed_search_paths(tmp_path):
    """A guard: even if discover() somehow produced a /search URL, is_job_detail_url must reject it before fetch."""
    assert not nc.is_job_detail_url("https://www.nature.com/naturecareers/jobs/search?q=phd")
    assert not nc.is_job_detail_url("https://www.nature.com/search")


# ---------------------------------------------------------------- F7 (PM live audit 2026-09-24) -----------------
def test_one_detail_url_failing_after_retries_never_loses_its_sibling_rows(tmp_path):
    """The real live bug: nature.com job 12864447 returned a 502 after retries and aborted the ENTIRE source,
    losing every sibling row already collected (pages_fetched left NULL in the ledger). One bad item must be
    counted and skipped, never propagated."""
    ok_url = "https://www.nature.com/naturecareers/job/12864861/associate-or-senior-editor/"
    bad_url = "https://www.nature.com/naturecareers/job/12864447/some-other-role/"
    Offline.pages = {ok_url: DETAIL}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([ok_url, bad_url])
    assert len(rows) == 1 and rows[0]["country_iso2"] == "DE"
    assert report["fetch_errors"] == 1 and report["attempted"] == 2
    Offline.fail_urls = set()


def test_every_detail_url_failing_raises_so_the_scheduler_still_sees_a_real_outage(tmp_path):
    """Never silently 'ok, zero rows' when literally nothing could be fetched - R1's 3-strikes counter and
    the run_ledger's own status='error'/'blocked' must still see this as a real failure."""
    bad_url = "https://www.nature.com/naturecareers/job/1/x/"
    Offline.pages = {}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    with pytest.raises(FetchFailed):
        c.crawl([bad_url])
    Offline.fail_urls = set()
