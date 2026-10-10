from datetime import datetime, timezone

import pytest

from ajp.connectors import academictransfer as at
from ajp.connectors.base import FetchFailed, FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)

SITEMAP = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.academictransfer.com/nl/jobs/1/adviseur-brandveiligheid/</loc></url>
<url><loc>https://www.academictransfer.com/en/jobs/2/phd-position-x/</loc></url>
<url><loc>https://www.academictransfer.com/nl/jobs/2/phd-positie-x/</loc></url>
</urlset>"""

PHD_DETAIL = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"WebPage","mainEntity":{"@type":"JobPosting",
 "title":"PhD Position X","datePosted":"2026-04-13T00:00:00+02:00",
 "jobLocation":{"address":{"addressCountry":"NL","addressLocality":"Leiden"}},
 "hiringOrganization":{"name":"Leiden University"}}}
</script></head><body><a href="mailto:hr@leiden.invalid">Contact HR</a></body></html>"""

NON_ACADEMIC_DETAIL = """<html><head><script type="application/ld+json">
{"@type":"JobPosting","title":"Fire Safety Advisor",
 "jobLocation":{"address":{"addressCountry":"NL"}},
 "hiringOrganization":{"name":"Leiden University"}}
</script></head><body></body></html>"""


class Offline(at.AcademicTransferConnector):
    """Serves fixtures through the real fetch_raw storage path (envelope included) without network."""
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


def test_discover_prefers_en_when_both_language_variants_share_an_id(tmp_path):
    Offline.pages = {at.SITEMAP_URL: SITEMAP}
    c = Offline(fast(), LocalFsStore(tmp_path))
    urls = c.discover()
    assert "https://www.academictransfer.com/nl/jobs/1/adviseur-brandveiligheid/" in urls   # id 1 has no /en/ variant here
    assert "https://www.academictransfer.com/en/jobs/2/phd-position-x/" in urls             # id 2: /en/ preferred over /nl/
    assert not any("/nl/jobs/2/" in u for u in urls)
    assert len(urls) == 2                                                                   # one URL per distinct id


def test_manifest_refuses_account_and_apply_paths():
    assert not at.MANIFEST.permits("https://www.academictransfer.com/account/settings")
    assert not at.MANIFEST.permits("https://www.academictransfer.com/apply/2/")
    assert at.MANIFEST.permits("https://www.academictransfer.com/en/jobs/2/phd-position-x/")


def test_crawl_extracts_and_classifies_kept_and_discarded(tmp_path):
    url = "https://www.academictransfer.com/en/jobs/2/phd-position-x/"
    Offline.pages = {url: PHD_DETAIL}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([url])
    assert report == {"attempted": 1, "parsed": 1, "no_jobposting": 0, "kept": 1, "discarded": 0, "fetch_errors": 0}
    assert rows[0]["kind"] == "employment" and rows[0]["position_kind"] == "phd"
    assert rows[0]["country_iso2"] == "NL" and rows[0]["ad_lang"] == "en"
    # T26 (packet 015): the detail page's own real mailto: link reaches the row (wired via jobposting_to_row's
    # new html= param, real end to end through this connector's own crawl() loop, not just unit-tested
    # against jobposting_to_row directly).
    assert rows[0]["contact_emails"] == ["hr@leiden.invalid"]
    assert rows[0]["apply_url"] == url


def test_crawl_discards_non_academic_role(tmp_path):
    url = "https://www.academictransfer.com/nl/jobs/1/adviseur-brandveiligheid/"
    Offline.pages = {url: NON_ACADEMIC_DETAIL}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([url])
    assert report["discarded"] == 1 and rows[0]["kind"] == "other"
    assert rows[0]["ad_lang"] == "nl"


# ---------------------------------------------------------------- F7 (PM live audit 2026-09-24) -----------------
def test_one_detail_url_failing_after_retries_never_loses_its_sibling_rows(tmp_path):
    ok_url = "https://www.academictransfer.com/en/jobs/2/phd-position-x/"
    bad_url = "https://www.academictransfer.com/en/jobs/3/some-other-role/"
    Offline.pages = {ok_url: PHD_DETAIL}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([ok_url, bad_url])
    assert len(rows) == 1 and rows[0]["country_iso2"] == "NL"
    assert report["fetch_errors"] == 1 and report["attempted"] == 2
    Offline.fail_urls = set()


def test_every_detail_url_failing_raises_so_the_scheduler_still_sees_a_real_outage(tmp_path):
    bad_url = "https://www.academictransfer.com/en/jobs/4/x/"
    Offline.pages = {}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    with pytest.raises(FetchFailed):
        c.crawl([bad_url])
    Offline.fail_urls = set()
