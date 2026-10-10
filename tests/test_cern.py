from datetime import datetime, timezone

import pytest

from ajp.connectors import cern
from ajp.connectors.base import FetchFailed, FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Mechanical Technician</title><link>https://careers.cern/jobs/mechanical-technician-3/</link>
<pubDate>Fri, 11 Sep 2026 12:38:44 +0000</pubDate><description><![CDATA[]]></description></item>
<item><title>Research Fellow in Particle Physics</title><link>https://careers.cern/jobs/research-fellow-pp/</link>
<pubDate>Fri, 12 Sep 2026 09:00:00 +0000</pubDate><description><![CDATA[]]></description></item>
</channel></rss>"""

DETAIL_TECHNICIAN = """<html><body><script>var x = "location: nowhere";</script>
<div class="location">Geneva, Switzerland</div>
<main><h1>Mechanical Technician</h1>
<p>As a mechanical technician, you will assemble models and prototypes.</p>
<p>Apply now</p><p>Before 11/10/2026 at 23:59 (Geneva Time)</p></main></body></html>"""

DETAIL_FELLOW = """<html><body>
<div class="location">Geneva, Switzerland</div>
<main><h1>Research Fellow in Particle Physics</h1>
<p>We are seeking a postdoctoral research fellow to join our particle physics group.</p>
<p>Before 01/01/2020 at 23:59 (Geneva Time)</p>
<p>Contact: <a href="mailto:jobs@cern.invalid">jobs@cern.invalid</a></p>
</main></body></html>"""    # already-passed deadline, on purpose


class Offline(cern.CernConnector):
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


def test_parse_rss_extracts_title_link_pubdate():
    items = cern.parse_rss(RSS)
    assert len(items) == 2
    assert items[0]["title"] == "Mechanical Technician"
    assert items[0]["link"] == "https://careers.cern/jobs/mechanical-technician-3/"


def test_parse_detail_resolves_location_strips_script_and_finds_deadline():
    d = cern.parse_detail(DETAIL_TECHNICIAN)
    assert d["city"] == "Geneva" and d["country_iso2"] == "CH"
    assert "nowhere" not in d["body_text"]                       # script content never leaks into raw_text
    assert d["deadline"] == datetime(2026, 10, 11, 23, 59, tzinfo=timezone.utc)


def test_discover_and_crawl_classifies_technician_as_other_and_fellow_as_employment(tmp_path):
    Offline.pages = {
        cern.RSS_URL: RSS,
        "https://careers.cern/jobs/mechanical-technician-3/": DETAIL_TECHNICIAN,
        "https://careers.cern/jobs/research-fellow-pp/": DETAIL_FELLOW,
    }
    c = Offline(fast(), LocalFsStore(tmp_path))
    items = c.discover()
    rows, report = c.crawl(items)
    assert report == {"attempted": 2, "parsed": 2, "kept": 1, "discarded": 1, "no_country": 0, "fetch_errors": 0}
    by_title = {r["title_raw"]: r for r in rows}
    assert by_title["Mechanical Technician"]["kind"] == "other"
    fellow = by_title["Research Fellow in Particle Physics"]
    # 'postdoctoral' in the body wins over the title's own 'Fellow' - the more specific rank, checked first
    assert fellow["kind"] == "employment" and fellow["position_kind"] == "postdoc"
    assert fellow["status"] == "expired"                          # its 'Before 01/01/2020' deadline has passed
    # T26 (packet 015)
    assert fellow["contact_emails"] == ["jobs@cern.invalid"]
    assert fellow["apply_url"] == "https://careers.cern/jobs/research-fellow-pp/"


def test_manifest_refuses_the_disallowed_smartrecruiters_api():
    assert not cern.MANIFEST.permits("https://api.smartrecruiters.com/v1/companies/CERN/postings")
    assert cern.MANIFEST.permits(cern.RSS_URL)


# ---------------------------------------------------------------- F7-class fix (found live, T26, 2026-09-25) ---
def test_one_detail_url_failing_after_retries_never_loses_its_sibling_rows(tmp_path):
    Offline.pages = {cern.RSS_URL: RSS, "https://careers.cern/jobs/research-fellow-pp/": DETAIL_FELLOW}
    Offline.fail_urls = {"https://careers.cern/jobs/mechanical-technician-3/"}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl(c.discover())
    assert len(rows) == 1 and rows[0]["title_raw"] == "Research Fellow in Particle Physics"
    assert report["fetch_errors"] == 1 and report["attempted"] == 2
    Offline.fail_urls = set()


def test_every_detail_url_failing_raises_so_the_scheduler_still_sees_a_real_outage(tmp_path):
    Offline.pages = {cern.RSS_URL: RSS}
    Offline.fail_urls = {"https://careers.cern/jobs/mechanical-technician-3/",
                         "https://careers.cern/jobs/research-fellow-pp/"}
    c = Offline(fast(), LocalFsStore(tmp_path))
    with pytest.raises(FetchFailed):
        c.crawl(c.discover())
    Offline.fail_urls = set()
