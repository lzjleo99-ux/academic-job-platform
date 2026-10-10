"""T18 acceptance: 'gen_sitemap_html ... with two configured seed sources and fixtures'. THEunijobs fixtures
are trimmed real samples captured 2026-09-23 (sitemapindex -> sitemap2-1.xml -> listing detail with real
schema.org JobPosting JSON-LD). The generic_html-mode fixture is a synthetic stand-in shaped like CNRS's own
real, live-verified pages (that host already has its own bespoke connector, cnrs.py - this only proves the
GENERIC engine's non-JSON-LD extraction path works, never a second registration of that same host).
"""
import json

import pytest

from ajp.connectors.base import FetchFailed, FetchResult, PoliteClient
from ajp.connectors.gen_sitemap_html import SitemapBoardConfig, SitemapHtmlConnector, _maybe_gunzip
from ajp.rawstore import LocalFsStore, put_raw
from datetime import datetime, timezone

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

THEUNIJOBS_SITEMAPINDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.timeshighereducation.com/unijobs/sitemap1-100.xml</loc></sitemap>
<sitemap><loc>https://www.timeshighereducation.com/unijobs/sitemap2-1.xml</loc></sitemap>
<sitemap><loc>https://www.timeshighereducation.com/unijobs/sitemap3-1.xml</loc></sitemap>
</sitemapindex>"""
# sitemap1-* (browse/facet pages) and sitemap3-* (employer pages) must never be descended into - only
# sitemap2-* holds actual /unijobs/listing/ detail URLs (PM-verified live shape, 2026-09-23).
THEUNIJOBS_SHARD_LISTINGS = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.timeshighereducation.com/unijobs/listing/415779/lecturer-in-criminology/</loc><lastmod>2026-09-23T03:51:20+00:00</lastmod></url>
<url><loc>https://www.timeshighereducation.com/unijobs/listing/415657/senior-lawyer-commercial/</loc><lastmod>2026-09-23T03:51:20+00:00</lastmod></url>
</urlset>"""
THEUNIJOBS_SHARD_BROWSE = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.timeshighereducation.com/unijobs/browse/criminology/</loc></url>
</urlset>"""
THEUNIJOBS_SHARD_EMPLOYER = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.timeshighereducation.com/unijobs/employer/university-of-auckland/</loc></url>
</urlset>"""

# Trimmed real JSON-LD (description shortened) captured live from the lecturer-in-criminology detail page.
LECTURER_DETAIL = f"""<html><head><script type="application/ld+json">{json.dumps({
    "@context": "https://schema.org/", "@type": "JobPosting", "title": "Lecturer in Criminology",
    "description": "We are seeking a Lecturer in Criminology to join our team.",
    "datePosted": "2026-09-23T01:48:00.000Z", "validThrough": "2026-10-18T22:59:00.000Z",
    "hiringOrganization": {"name": "UNIVERSITY OF AUCKLAND", "@type": "Organization"},
    "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressCountry": "NZ"}}],
    "employmentType": ["FULL_TIME"],
})}</script></head><body></body></html>"""
NON_ACADEMIC_DETAIL = f"""<html><head><script type="application/ld+json">{json.dumps({
    "@type": "JobPosting", "title": "Senior Lawyer (Commercial)",
    "description": "Commercial law role.", "jobLocation": {"address": {"addressCountry": "AU"}},
})}</script></head><body></body></html>"""

THEUNIJOBS_CONFIG = SitemapBoardConfig(
    source_id="gen_sitemap_html__test_theunijobs", host="www.timeshighereducation.com",
    sitemapindex_url="https://www.timeshighereducation.com/unijobs/sitemapindex.xml",
    shard_url_pattern=r"/unijobs/sitemap2-\d+\.xml$", detail_url_pattern=r"/unijobs/listing/\d+/",
    detail_mode="jsonld",
)

# ---------------------------------------------------------------- generic_html mode (CNRS-shaped fixture) -----
GENERIC_SITEMAPINDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://board.invalid/Sitemaps/OffresSiteMapProviderResult.ashx</loc></sitemap>
</sitemapindex>"""
GENERIC_SHARD = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://board.invalid/Offres/CDD/UMR1-postdoc/Default.aspx</loc></url>
<url><loc>https://board.invalid/Accueil/Default.aspx</loc></url>
</urlset>"""
GENERIC_DETAIL = """<html><body><h1>Postdoctoral Research Fellow</h1>
<article>We are recruiting a Postdoctoral Research Fellow for a research programme in physics.</article>
<p>Contact: <a href="mailto:recrutement@board.invalid">recrutement@board.invalid</a></p>
</body></html>"""

GENERIC_CONFIG = SitemapBoardConfig(
    source_id="gen_sitemap_html__test_generic", host="board.invalid",
    sitemapindex_url="https://board.invalid/Sitemaps/OffresSiteMapProviderResult.ashx",
    shard_url_pattern=r".*", detail_url_pattern=r"/Offres/",
    detail_mode="generic_html", classifier="intl", fixed_country="FR", org_raw="Test Research Org",
)


class Offline(SitemapHtmlConnector):
    pages = {}
    fail_urls = set()               # F7 regression: simulate a real per-item fetch failure (e.g. a 502)

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if url in self.fail_urls:
            raise FetchFailed(f"giving up on {url} after 3 attempts: simulated 502")
        body = self.pages.get(url, "").encode()
        status = 200 if url in self.pages else 404
        res = FetchResult(url=url, final_url=url, status=status, content=body, content_type="text/html", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=status, content_type="text/html", robots_allowed=True)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_maybe_gunzip_decompresses_a_real_gz_file_and_passes_through_plain_xml():
    import gzip as gz
    compressed = gz.compress(b"<urlset></urlset>")
    assert _maybe_gunzip("https://x.invalid/sitemap.xml.gz", compressed) == b"<urlset></urlset>"
    assert _maybe_gunzip("https://x.invalid/sitemap.xml", b"<urlset></urlset>") == b"<urlset></urlset>"


def test_discover_only_descends_into_shards_matching_the_configured_pattern_never_browse_or_employer(tmp_path):
    Offline.pages = {
        THEUNIJOBS_CONFIG.sitemapindex_url: THEUNIJOBS_SITEMAPINDEX,
        "https://www.timeshighereducation.com/unijobs/sitemap2-1.xml": THEUNIJOBS_SHARD_LISTINGS,
        # sitemap1-100/sitemap3-1 fixtures deliberately OMITTED - discover() must never even request them
    }
    c = Offline(fast(), LocalFsStore(tmp_path), THEUNIJOBS_CONFIG)
    urls = c.discover()
    assert urls == [
        "https://www.timeshighereducation.com/unijobs/listing/415779/lecturer-in-criminology/",
        "https://www.timeshighereducation.com/unijobs/listing/415657/senior-lawyer-commercial/",
    ]


def test_crawl_jsonld_mode_maps_a_real_jobposting_row(tmp_path):
    Offline.pages = {
        "https://www.timeshighereducation.com/unijobs/listing/415779/lecturer-in-criminology/": LECTURER_DETAIL,
    }
    c = Offline(fast(), LocalFsStore(tmp_path), THEUNIJOBS_CONFIG)
    rows, report = c.crawl(["https://www.timeshighereducation.com/unijobs/listing/415779/lecturer-in-criminology/"])
    assert len(rows) == 1
    row = rows[0]
    assert row["title_raw"] == "Lecturer in Criminology"
    assert row["country_iso2"] == "NZ"
    assert row["org_raw"] == "UNIVERSITY OF AUCKLAND"
    assert row["kind"] == "employment" and row["position_kind"] == "lecturer"
    assert row["ft_pt"] == "full"
    assert row["source_id"] == "gen_sitemap_html__test_theunijobs"


def test_crawl_jsonld_mode_skips_a_page_with_no_jobposting_markup(tmp_path):
    Offline.pages = {"https://x.invalid/no-jsonld": "<html><body>no markup here</body></html>"}
    c = Offline(fast(), LocalFsStore(tmp_path), THEUNIJOBS_CONFIG)
    rows, report = c.crawl(["https://x.invalid/no-jsonld"])
    assert rows == [] and report["no_country"] == 1


def test_crawl_generic_html_mode_maps_a_row_using_the_classifier_and_fixed_country(tmp_path):
    Offline.pages = {"https://board.invalid/Offres/CDD/UMR1-postdoc/Default.aspx": GENERIC_DETAIL}
    c = Offline(fast(), LocalFsStore(tmp_path), GENERIC_CONFIG)
    rows, report = c.crawl(["https://board.invalid/Offres/CDD/UMR1-postdoc/Default.aspx"])
    assert len(rows) == 1
    row = rows[0]
    assert row["title_raw"] == "Postdoctoral Research Fellow"
    assert row["country_iso2"] == "FR"
    assert row["kind"] == "employment" and row["position_kind"] == "postdoc"
    assert row["org_raw"] == "Test Research Org"
    assert row["extraction_family"] == "gen_sitemap_html"
    # T26 (packet 015)
    assert row["contact_emails"] == ["recrutement@board.invalid"]
    assert row["apply_url"] == "https://board.invalid/Offres/CDD/UMR1-postdoc/Default.aspx"


def test_generic_html_mode_without_fixed_country_persists_the_row_with_a_location_class(tmp_path):
    """T23 (PM ruling R2): no fixed_country configured is never guessed into a fabricated one (D6 unchanged) -
    but the row is persisted with country_iso2=None + a real location_class now, not discarded."""
    no_country = SitemapBoardConfig(source_id="gen_sitemap_html__test_nocountry", host="board.invalid",
                                     sitemapindex_url="https://board.invalid/sitemapindex.xml",
                                     detail_url_pattern=r"/Offres/", detail_mode="generic_html", fixed_country=None)
    Offline.pages = {"https://board.invalid/Offres/x": GENERIC_DETAIL}
    c = Offline(fast(), LocalFsStore(tmp_path), no_country)
    rows, report = c.crawl(["https://board.invalid/Offres/x"])
    assert len(rows) == 1
    assert rows[0]["country_iso2"] is None
    assert rows[0]["location_class"] == "unknown"
    assert report["no_country"] == 0 and report["kept"] == 1


def test_ensure_source_sets_ats_family_and_a_jsonld_config_stays_intl(conn):
    """T18 registry acceptance (gen_sitemap_html mirror of the gen_wordpress fix, found live 2026-09-23)."""
    SitemapHtmlConnector.ensure_source(conn, THEUNIJOBS_CONFIG)
    row = conn.execute("SELECT ats_family, tdm_reservation, geo_scope FROM source WHERE id = %s",
                       (THEUNIJOBS_CONFIG.source_id,)).fetchone()
    assert row == ("gen_sitemap_html", "none", "INTL")


def test_manifest_permits_shards_and_gz_shards_and_detail_pages_but_not_other_hosts(tmp_path):
    c = SitemapHtmlConnector(fast(), LocalFsStore(tmp_path), THEUNIJOBS_CONFIG)
    assert c.manifest.permits(THEUNIJOBS_CONFIG.sitemapindex_url)
    assert c.manifest.permits("https://www.timeshighereducation.com/unijobs/sitemap2-1.xml")
    assert c.manifest.permits("https://www.timeshighereducation.com/some/shard.xml.gz")
    assert c.manifest.permits("https://www.timeshighereducation.com/unijobs/listing/1/x/")
    assert not c.manifest.permits("https://attacker.invalid/unijobs/listing/1/x/")


# ---------------------------------------------------------------- F7/F9 (PM live audit 2026-09-24) --------------
def test_one_detail_url_failing_after_retries_never_loses_its_sibling_rows(tmp_path):
    """The real live bug: gen_sitemap_html__theunijobs (listing 413869) got a 502 on one detail URL and
    aborted the whole sweep, losing every sibling row already collected."""
    ok_url = "https://www.timeshighereducation.com/unijobs/listing/415779/lecturer-in-criminology/"
    bad_url = "https://www.timeshighereducation.com/unijobs/listing/413869/some-other-role/"
    Offline.pages = {ok_url: LECTURER_DETAIL}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path), THEUNIJOBS_CONFIG)
    rows, report = c.crawl([ok_url, bad_url])
    assert len(rows) == 1 and rows[0]["country_iso2"] == "NZ"
    assert report["fetch_errors"] == 1 and report["attempted"] == 2
    Offline.fail_urls = set()


def test_every_detail_url_failing_raises_so_the_scheduler_still_sees_a_real_outage(tmp_path):
    bad_url = "https://www.timeshighereducation.com/unijobs/listing/1/x/"
    Offline.pages = {}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path), THEUNIJOBS_CONFIG)
    with pytest.raises(FetchFailed):
        c.crawl([bad_url])
    Offline.fail_urls = set()


def test_a_non_200_sitemapindex_raises_never_silently_decoded_as_empty_xml(tmp_path):
    """F9's own failure shape: a challenge/maintenance page in place of the real sitemap index must never be
    gunzip-decoded and regex-scanned as if it were real XML - that just matches zero <loc> tags and reports a
    clean, empty 'nothing new today' run forever, hiding a real, ongoing failure."""
    Offline.pages = {}   # sitemapindex_url absent -> Offline's own fetch_raw returns status=404
    c = Offline(fast(), LocalFsStore(tmp_path), THEUNIJOBS_CONFIG)
    with pytest.raises(FetchFailed):
        c.discover()


def test_a_non_200_shard_raises_never_silently_decoded_as_empty_xml(tmp_path):
    Offline.pages = {THEUNIJOBS_CONFIG.sitemapindex_url: THEUNIJOBS_SITEMAPINDEX}   # shard itself absent -> 404
    c = Offline(fast(), LocalFsStore(tmp_path), THEUNIJOBS_CONFIG)
    with pytest.raises(FetchFailed):
        c.discover()
