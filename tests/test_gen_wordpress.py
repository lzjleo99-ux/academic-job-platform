"""T18 acceptance: 'gen_wordpress ... with two configured seed sources and fixtures; classifier discards
non-academic rows after raw capture marked excluded'. Fixtures below are trimmed real samples captured
2026-09-23 (careers.cern/jobs/feed/ shape - used here purely as a validation fixture proving the GENERIC
engine reproduces cern.py's own real, bespoke behaviour; the live-registered seeds for this family are
sljaka.com and fondzanauku.gov.rs's calls feed, see tools/dev_up.py - registering CERN a second time under
this family too would double-ingest a host cern.py already owns).
"""
from datetime import timezone

from ajp.connectors.base import FetchResult, PoliteClient
from ajp.connectors.gen_wordpress import WordPressBoardConfig, WordPressConnector, candidate_feed_urls, parse_feed_items
from ajp.rawstore import LocalFsStore, put_raw
from datetime import datetime

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

CERN_FEED = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
	xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
	<title>Job offers - careers.cern</title>
	<item>
		<title>Mechanical Technician</title>
		<link>https://careers.cern/jobs/mechanical-technician-3/</link>
		<pubDate>Fri, 11 Sep 2026 12:38:44 +0000</pubDate>
		<description><![CDATA[]]></description>
		<content:encoded><![CDATA[]]></content:encoded>
	</item>
	<item>
		<title>Postdoctoral Research Fellow</title>
		<link>https://careers.cern/jobs/postdoctoral-research-fellow/</link>
		<pubDate>Fri, 11 Sep 2026 12:15:39 +0000</pubDate>
		<description><![CDATA[]]></description>
		<content:encoded><![CDATA[]]></content:encoded>
	</item>
</channel></rss>"""

CERN_DETAIL_POSTDOC = """<html><body><main>
<p class="location">Geneva, Switzerland</p>
<article>We are seeking a Postdoctoral Research Fellow to join our physics research programme at CERN.</article>
<p>Contact: <a href="mailto:recruitment@cern.invalid">recruitment@cern.invalid</a></p>
</main></body></html>"""
CERN_DETAIL_TECHNICIAN = """<html><body><main>
<p class="location">Geneva, Switzerland</p>
<article>We need a Mechanical Technician to support engineering operations.</article>
</main></body></html>"""

# CERN's real feed lives at /jobs/feed/, not the generic default /feed/ - a config for this host needs to say so.
CERN_CONFIG = WordPressBoardConfig(source_id="gen_wordpress__test_cern", host="careers.cern",
                                   org_raw="CERN", classifier="intl", fixed_country="CH",
                                   feed_paths=("/feed/",), category_feed_paths=("/jobs/feed/",))

SR_FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Naučni saradnik - Institut za fiziku</title>
<link>https://board.invalid/oglas/1</link><pubDate>Mon, 21 Sep 2026 08:00:00 +0000</pubDate>
<description><![CDATA[]]></description></item>
<item><title>Prodavac u maloprodajnom objektu</title>
<link>https://board.invalid/oglas/2</link><pubDate>Mon, 21 Sep 2026 08:00:00 +0000</pubDate>
<description><![CDATA[]]></description></item>
</channel></rss>"""
SR_DETAIL = """<html><body><article>Fakultet raspisuje konkurs za izbor u zvanje naucnog saradnika.</article></body></html>"""
SR_CONFIG = WordPressBoardConfig(source_id="gen_wordpress__test_rs", host="board.invalid",
                                 org_raw="Test Faculty", classifier="rs", fixed_country="RS")


class Offline(WordPressConnector):
    """Serves fixtures through the real fetch_raw storage path, keyed by URL - a URL with no fixture entry
    behaves like a real 404 (feed-path discovery tries several candidates and must treat a miss as 'try the
    next one', never as a test-harness crash)."""
    pages = {}

    def fetch_raw(self, url, kind="html", max_bytes=None):
        body = self.pages.get(url)
        status, content = (200, body.encode()) if body is not None else (404, b"not found")
        res = FetchResult(url=url, final_url=url, status=status, content=content, content_type="text/html", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=content, fetched_at=NOW,
                      status=status, content_type="text/html", robots_allowed=True)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_parse_feed_items_extracts_title_link_content():
    items = parse_feed_items(CERN_FEED)
    assert len(items) == 2
    assert items[0]["title"] == "Mechanical Technician"
    assert items[1]["link"] == "https://careers.cern/jobs/postdoctoral-research-fellow/"


def test_candidate_feed_urls_is_pure_and_ordered():
    assert candidate_feed_urls(CERN_CONFIG) == ["https://careers.cern/feed/", "https://careers.cern/jobs/feed/"]


def test_f10_parse_feed_items_decodes_html_entities_in_title_link_and_description():
    """T29 (packet 017, PM-verified live): this module regex-parses raw RSS XML directly (never a real
    parser) - a real feed item's title/link/description can carry '&amp;'/'&quot;' exactly like
    higher_ed_jobsrss.py's identical regex shape did before that fix."""
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>R&amp;D Technician - Company &quot;BCILITY&quot;</title>
    <link>https://board.invalid/oglas/1?ref=feed&amp;utm=rss</link>
    <pubDate>Mon, 21 Sep 2026 08:00:00 +0000</pubDate>
    <description><![CDATA[Reports to R&amp;D lead]]></description></item>
    </channel></rss>"""
    items = parse_feed_items(feed)
    assert items[0]["title"] == 'R&D Technician - Company "BCILITY"'
    assert items[0]["link"] == "https://board.invalid/oglas/1?ref=feed&utm=rss"
    assert items[0]["description"] == "Reports to R&D lead"


def test_discover_feed_tries_paths_in_order_via_fetch_raw_and_finds_the_real_one(tmp_path):
    Offline.pages = {"https://careers.cern/jobs/feed/": CERN_FEED}    # /feed/ (tried first) has no fixture -> 404
    c = Offline(fast(), LocalFsStore(tmp_path), CERN_CONFIG)
    assert c.discover_feed() == "https://careers.cern/jobs/feed/"


def test_discover_feed_returns_none_when_every_candidate_404s(tmp_path):
    Offline.pages = {}
    c = Offline(fast(), LocalFsStore(tmp_path), CERN_CONFIG)
    assert c.discover_feed() is None


def test_discover_feed_never_trusts_a_200_that_is_not_actually_feed_shaped(tmp_path):
    """packet 009: a feed path could 200 with an ordinary HTML page (the robots.txt-looks-like-HTML
    precedent) - never trust the status code alone."""
    Offline.pages = {"https://careers.cern/feed/": "<html><body>not a feed</body></html>"}
    c = Offline(fast(), LocalFsStore(tmp_path), CERN_CONFIG)
    assert c.discover_feed() is None


def test_classifier_gate_discards_non_academic_titles_intl(tmp_path):
    Offline.pages = {"https://careers.cern/jobs/feed/": CERN_FEED,
                     "https://careers.cern/jobs/mechanical-technician-3/": CERN_DETAIL_TECHNICIAN,
                     "https://careers.cern/jobs/postdoctoral-research-fellow/": CERN_DETAIL_POSTDOC}
    c = Offline(fast(), LocalFsStore(tmp_path), CERN_CONFIG)
    items = c.discover()
    # 'Mechanical Technician' never clears the EN academic-keyword title gate - never even fetched for detail
    assert len(items) == 1 and items[0]["title"] == "Postdoctoral Research Fellow"


def test_crawl_maps_the_surviving_item_with_body_from_the_detail_page_and_posted_at(tmp_path):
    Offline.pages = {"https://careers.cern/jobs/feed/": CERN_FEED,
                     "https://careers.cern/jobs/postdoctoral-research-fellow/": CERN_DETAIL_POSTDOC}
    c = Offline(fast(), LocalFsStore(tmp_path), CERN_CONFIG)
    rows, report = c.crawl()
    assert len(rows) == 1
    row = rows[0]
    assert row["title_raw"] == "Postdoctoral Research Fellow"
    assert row["country_iso2"] == "CH"
    assert row["kind"] == "employment" and row["position_kind"] == "postdoc"
    assert "physics research programme" in row["raw_text"]
    assert row["source_id"] == "gen_wordpress__test_cern"
    # S2 (PM code review 2026-09-23): pubDate 'Fri, 11 Sep 2026 12:15:39 +0000' -> posted_at
    assert row["posted_at"].isoformat().startswith("2026-09-11T12:15:39")
    # T26 (packet 015)
    assert row["contact_emails"] == ["recruitment@cern.invalid"]
    assert row["apply_url"] == "https://careers.cern/jobs/postdoctoral-research-fellow/"


def test_rs_classifier_config_uses_rs_text_and_fixed_country(tmp_path):
    Offline.pages = {"https://board.invalid/feed/": SR_FEED,
                     "https://board.invalid/oglas/1": SR_DETAIL}
    c = Offline(fast(), LocalFsStore(tmp_path), SR_CONFIG)
    rows, report = c.crawl()
    assert len(rows) == 1
    row = rows[0]
    assert row["country_iso2"] == "RS"
    assert row["kind"] == "employment"
    assert row["position_kind"] == "researcher_title"
    assert row["rs_research_title"] == "naucni_saradnik"


def test_default_kind_bypasses_the_title_gate_and_fixes_kind(tmp_path):
    """fondzanauku.gov.rs's own '/category/calls/' feed (PM-verified live 2026-09-23): every title is a
    funding call ('Public Call for the Joint Research Program...') and never matches the employment keyword
    gate - default_kind='grant_call' must let the item through AND set its kind, without ever running the
    ordinary classifier (which would default to 'other' on this title, hiding a genuine listing)."""
    grant_feed = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Public Call for the Joint Research Program of Scientists from Serbia and China</title>
<link>https://fund.invalid/call-1/</link><pubDate>Mon, 24 Aug 2026 14:07:04 +0000</pubDate>
<description><![CDATA[]]></description>
<content:encoded><![CDATA[Full call text describing eligibility and deadlines.]]></content:encoded></item>
</channel></rss>"""
    config = WordPressBoardConfig(source_id="gen_wordpress__test_fund", host="fund.invalid",
                                  org_raw="Science Fund", classifier="intl", fixed_country="RS",
                                  feed_paths=(), category_feed_paths=("/category/calls/feed/",),
                                  default_kind="grant_call")
    Offline.pages = {"https://fund.invalid/category/calls/feed/": grant_feed}   # no detail page fixture -> 404, feed content used
    c = Offline(fast(), LocalFsStore(tmp_path), config)
    items = c.discover()
    assert len(items) == 1                                       # never dropped by the title gate
    rows, report = c.crawl(items)
    assert len(rows) == 1 and rows[0]["kind"] == "grant_call"
    assert "eligibility and deadlines" in rows[0]["raw_text"]     # fell back to feed content:encoded


def test_manifest_permits_http_scheme_and_query_string_detail_links(tmp_path):
    """Found live 2026-09-23: fondzanauku.gov.rs's own <link> elements are plain http:// with a trailing
    '?lang=en' - the manifest's detail-page pattern originally required https:// and excluded '?' entirely,
    silently refusing every one of that real source's own links."""
    config = WordPressBoardConfig(source_id="gen_wordpress__test_fund2", host="fondzanauku.gov.rs")
    manifest = WordPressConnector(fast(), LocalFsStore(tmp_path), config).manifest
    assert manifest.permits("http://fondzanauku.gov.rs/2026/08/some-call/?lang=en")
    assert manifest.permits("https://fondzanauku.gov.rs/2026/08/some-call/")


def test_ensure_source_sets_ats_family_and_geo_scope_from_the_config(conn):
    """T18 registry acceptance: without ats_family set, route_type_label() can't tell a gen_wordpress source
    apart from an unclassified one (found live 2026-09-23 re-checking this module's own registration path)."""
    config = WordPressBoardConfig(source_id="gen_wordpress__test_registry", host="registry.invalid",
                                  classifier="rs", fixed_country="RS")
    WordPressConnector.ensure_source(conn, config, "https://registry.invalid/feed/")
    row = conn.execute("SELECT ats_family, tdm_reservation, geo_scope FROM source WHERE id = %s",
                       (config.source_id,)).fetchone()
    assert row == ("gen_wordpress", "none", "RS")


def test_ensure_source_persists_the_full_config_for_the_t21_scheduler_factory_to_reconstruct(conn):
    """T21, found live (the first real ajp-run --once against a real seed): ensure_source() never wrote its
    own config to source.config at all (a column that did not exist when this method was first written) -
    every gen_wordpress source's own board configuration silently stayed '{}' forever, and
    ajp.scheduler.build_connector crashed on the very first real run trying to reconstruct
    WordPressBoardConfig(**{}). Round-trips through a REAL DB row, not just the ensure_source call, so a
    regression here would be caught even if only the SQL text (not this test) changed back."""
    from ajp import scheduler
    from ajp.connectors.base import PoliteClient
    from ajp.rawstore import LocalFsStore
    import tempfile

    config = WordPressBoardConfig(source_id="gen_wordpress__test_config_roundtrip", host="roundtrip.invalid",
                                  org_raw="Roundtrip Org", classifier="rs", fixed_country="RS",
                                  category_feed_paths=("/category/oglasi/feed/",))
    WordPressConnector.ensure_source(conn, config, "https://roundtrip.invalid/category/oglasi/feed/")
    from psycopg.rows import dict_row
    row = conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM source WHERE id = %s", (config.source_id,)).fetchone()
    assert row["config"]["host"] == "roundtrip.invalid"
    assert row["config"]["category_feed_paths"] == ["/category/oglasi/feed/"]     # JSON array, coerced back by the factory

    connector = scheduler.build_connector(row, PoliteClient(rps=1000, sleep=lambda s: None), LocalFsStore(tempfile.mkdtemp()))
    assert isinstance(connector, WordPressConnector)
    assert connector.config == config                                            # a full, real round-trip, field for field


def test_a_config_without_fixed_country_persists_the_row_with_a_location_class(tmp_path):
    """T23 (PM ruling R2): a config without fixed_country is never guessed into a fabricated one (D6
    unchanged) - but the row is persisted with country_iso2=None + a real location_class now, not discarded."""
    no_country_config = WordPressBoardConfig(source_id="gen_wordpress__test_nocountry", host="careers.cern",
                                              classifier="intl", fixed_country=None,
                                              feed_paths=(), category_feed_paths=("/jobs/feed/",))
    Offline.pages = {"https://careers.cern/jobs/feed/": CERN_FEED,
                     "https://careers.cern/jobs/postdoctoral-research-fellow/": CERN_DETAIL_POSTDOC}
    c = Offline(fast(), LocalFsStore(tmp_path), no_country_config)
    rows, report = c.crawl()
    assert len(rows) == 1
    assert rows[0]["country_iso2"] is None
    assert rows[0]["location_class"] == "unknown"
    assert report["no_country"] == 0
