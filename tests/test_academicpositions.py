from datetime import datetime, timezone

import pytest

from ajp.connectors import academicpositions as ap
from ajp.connectors.base import FetchFailed, FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)

SHARD = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://academicpositions.com/ad/university-of-twente/2022/phd-ic-design/188333</loc></url>
</urlset>"""

DETAIL = """<html><head><script type="application/ld+json">
{"@type":"JobPosting","title":"PhD Position in IC Design Group",
 "hiringOrganization":{"name":"University of Twente"},
 "jobLocation":{"address":{"addressCountry":"NL","addressLocality":"Enschede"}}}
</script></head><body></body></html>"""


class Offline(ap.AcademicPositionsConnector):
    pages = {}
    fetched = []
    fail_urls = set()               # F7 regression: simulate a real per-item fetch failure (e.g. a 502)
    status_overrides = {}           # F9 regression: simulate a real non-200 response (e.g. a Cloudflare 403)

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        if url in self.fail_urls:
            raise FetchFailed(f"giving up on {url} after 3 attempts: simulated 502")
        status = self.status_overrides.get(url, 200)
        body = self.pages.get(url, "<html>not found</html>").encode()
        res = FetchResult(url=url, final_url=url, status=status, content=body, content_type="text/html", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=status, content_type="text/html", robots_allowed=True)
        Offline.fetched.append(url)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_discover_and_crawl(tmp_path):
    Offline.pages = {
        ap.JOBS_SHARD_URL: SHARD,
        "https://academicpositions.com/ad/university-of-twente/2022/phd-ic-design/188333": DETAIL,
    }
    c = Offline(fast(), LocalFsStore(tmp_path))
    urls = c.discover()
    assert urls == ["https://academicpositions.com/ad/university-of-twente/2022/phd-ic-design/188333"]
    rows, report = c.crawl(urls)
    assert report == {"attempted": 1, "parsed": 1, "no_jobposting": 0, "kept": 1, "discarded": 0, "fetch_errors": 0}
    assert rows[0]["kind"] == "employment" and rows[0]["country_iso2"] == "NL"


# ---------------------------------------------------------------- F7 (PM live audit 2026-09-24) -----------------
def test_one_detail_url_failing_after_retries_never_loses_its_sibling_rows(tmp_path):
    ok_url = "https://academicpositions.com/ad/university-of-twente/2022/phd-ic-design/188333"
    bad_url = "https://academicpositions.com/ad/some-org/2022/some-other-role/999999"
    Offline.pages = {ok_url: DETAIL}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([ok_url, bad_url])
    assert len(rows) == 1 and rows[0]["country_iso2"] == "NL"
    assert report["fetch_errors"] == 1 and report["attempted"] == 2
    Offline.fail_urls = set()


def test_every_detail_url_failing_raises_so_the_scheduler_still_sees_a_real_outage(tmp_path):
    bad_url = "https://academicpositions.com/ad/some-org/2022/x/1"
    Offline.pages = {}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    with pytest.raises(FetchFailed):
        c.crawl([bad_url])
    Offline.fail_urls = set()


# ---------------------------------------------------------------- F9 (PM live audit 2026-09-24) -----------------
def test_a_cloudflare_challenge_page_on_the_shard_url_raises_never_a_silent_empty_discover(tmp_path):
    """The real live finding: academicpositions.com's shard URL started returning a genuine Cloudflare 403
    'Just a moment...' challenge page (real content-type text/html, no <loc> tags at all) - decoded and
    regex-scanned for <loc> anyway before this fix, it silently matched zero URLs every run, reporting a
    clean 'nothing new today' with no visible failure. Must raise instead, so the scheduler's own R1
    3-strikes counter and ledger status='error'/'blocked' see the real, ongoing outage."""
    Offline.pages = {ap.JOBS_SHARD_URL: "<!DOCTYPE html><html><title>Just a moment...</title></html>"}
    Offline.status_overrides = {ap.JOBS_SHARD_URL: 403}
    c = Offline(fast(), LocalFsStore(tmp_path))
    with pytest.raises(FetchFailed):
        c.discover()
    Offline.status_overrides = {}
