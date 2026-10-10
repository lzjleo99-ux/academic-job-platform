from datetime import datetime, timezone

import pytest

from ajp.connectors import jobs_ac_uk as jau
from ajp.connectors.base import FetchFailed, FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)

SITEMAPINDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.jobs.ac.uk/sitemap0.xml</loc></sitemap>
</sitemapindex>"""

SHARD = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>www.jobs.ac.uk</loc></url>
<url><loc>www.jobs.ac.uk/search</loc></url>
<url><loc>www.jobs.ac.uk/country-profiles/germany</loc></url>
<url><loc>www.jobs.ac.uk/job/DQH648/faculty-position</loc></url>
<url><loc>www.jobs.ac.uk/job/feedback/x</loc></url>
</urlset>"""

DETAIL = """<html><head><script type="application/ld+json">
{"@type":"JobPosting","title":"Faculty Position in Finance and Accounting",
 "description":"<p>Position: Assistant/Associate/Full Professor.</p>",
 "hiringOrganization":{"name":"Shenzhen Technology University"},
 "jobLocation":[{"address":{"addressLocality":"Shenzhen","addressCountry":"China"}}]}
</script></head><body></body></html>"""


class Offline(jau.JobsAcUkConnector):
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
    ("https://www.jobs.ac.uk/job/DQH648/faculty-position", True),
    ("https://www.jobs.ac.uk/search", False),
    ("https://www.jobs.ac.uk/country-profiles/germany", False),
    ("https://www.jobs.ac.uk/job/feedback/x", False),
    ("https://www.jobs.ac.uk/enhanced/fp/x", False),
])
def test_is_job_detail_url(url, expected):
    assert jau.is_job_detail_url(url) is expected


def test_discover_follows_index_to_shard_and_filters_to_job_details(tmp_path):
    Offline.pages = {jau.SITEMAPINDEX_URL: SITEMAPINDEX, "https://www.jobs.ac.uk/sitemap0.xml": SHARD}
    c = Offline(fast(), LocalFsStore(tmp_path))
    urls = c.discover()
    assert urls == ["https://www.jobs.ac.uk/job/DQH648/faculty-position"]     # feedback/search/country-profile excluded


def test_full_country_name_resolves_and_html_description_is_stripped(tmp_path):
    url = "https://www.jobs.ac.uk/job/DQH648/faculty-position"
    Offline.pages = {url: DETAIL}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([url])
    assert report == {"attempted": 1, "parsed": 1, "no_jobposting": 0, "kept": 1, "discarded": 0, "fetch_errors": 0}
    assert rows[0]["country_iso2"] == "CN" and rows[0]["position_kind"] == "professor"
    assert "<p>" not in rows[0]["raw_text"]


# ---------------------------------------------------------------- F7 (PM live audit 2026-09-24) -----------------
def test_one_detail_url_failing_after_retries_never_loses_its_sibling_rows(tmp_path):
    ok_url = "https://www.jobs.ac.uk/job/DQH648/faculty-position"
    bad_url = "https://www.jobs.ac.uk/job/DSK277/some-other-role"
    Offline.pages = {ok_url: DETAIL}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([ok_url, bad_url])
    assert len(rows) == 1 and rows[0]["country_iso2"] == "CN"
    assert report["fetch_errors"] == 1 and report["attempted"] == 2
    Offline.fail_urls = set()


def test_every_detail_url_failing_raises_so_the_scheduler_still_sees_a_real_outage(tmp_path):
    bad_url = "https://www.jobs.ac.uk/job/X/x"
    Offline.pages = {}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    with pytest.raises(FetchFailed):
        c.crawl([bad_url])
    Offline.fail_urls = set()
