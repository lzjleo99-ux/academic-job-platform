"""T19 acceptance: KTH with recorded fixtures (trimmed real samples from www.kth.se/lediga-jobb/rss/ and a
real detail page, captured 2026-09-23). The detail-page fixtures are ENGLISH, matching what this project's
own client actually receives live (Accept-Language: en is honoured by the site) - the feed's own Swedish
<title> is discovery-only and never used as title_raw (see kth.py's own module docstring)."""
from datetime import datetime, timezone

from ajp.connectors import kth
from ajp.connectors.base import FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

FEED = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
<title>Nya lediga jobb hos  KTH Royal Institute of Technology</title>
<item><title><![CDATA[HR-generalist till KTH (vikariat)]]></title>
<link>https://kth.se/lediga-jobb/963817</link><guid>https://kth.se/lediga-jobb/963817</guid>
<pubDate>Thu, 24 Sep 2026 21:59:59 GMT</pubDate>
<description><![CDATA[Läs mer på:  https://kth.se/lediga-jobb/963817]]></description></item>
<item><title><![CDATA[Doktorand inom akustisk källkarakterisering]]></title>
<link>https://kth.se/lediga-jobb/962556</link><guid>https://kth.se/lediga-jobb/962556</guid>
<pubDate>Thu, 24 Sep 2026 21:59:59 GMT</pubDate>
<description><![CDATA[Läs mer på:  https://kth.se/lediga-jobb/962556]]></description></item>
</channel></rss>"""

# Trimmed real detail page shape (English, label/value block byte-identical to the live page's own markup
# when fetched with Accept-Language: en, as this project's own client always sends).
DETAIL_DOCTORAL = """<html><body><main>
<h1>Doctoral student in acoustic source characterisation</h1><h2>KTH Royal Institute of Technology, School of Engineering Sciences</h2>
<div class="job-details-content"><div class="details-main-col">
<h3>Project description</h3><p>The position is part of a project on acoustic source characterisation of electric vehicle components.</p>
</div><div class="details-secondary-col">
<div class="details-list"><b>Type of employment</b><p>Temporary position</p></div>
<div class="details-list"><b>Contract type</b><p>Full time</p></div>
<div class="details-list"><b>Location</b><p>Stockholm</p></div>
<div class="details-list"><b>Country</b><p>Sweden</p></div>
<div class="details-list"><b>Published</b><p>03.Sep.2026</p></div>
<div class="details-list"><b>Last application date</b><p>24.Sep.2026</p></div>
</div></div>
<p>Contact: <a href="mailto:recruiter@kth.invalid">recruiter@kth.invalid</a></p>
</main></body></html>"""

DETAIL_HR = """<html><body><main>
<h1>HR generalist at KTH (temporary)</h1><h2>KTH Royal Institute of Technology, HR Office</h2>
<div class="job-details-content"><div class="details-main-col">
<h3>About the job</h3><p>We are looking for an HR generalist focusing on advice and development.</p>
</div><div class="details-secondary-col">
<div class="details-list"><b>Type of employment</b><p>Permanent position</p></div>
<div class="details-list"><b>Contract type</b><p>Full time</p></div>
<div class="details-list"><b>Location</b><p>Stockholm</p></div>
<div class="details-list"><b>Country</b><p>Sweden</p></div>
<div class="details-list"><b>Published</b><p>01.Sep.2026</p></div>
<div class="details-list"><b>Last application date</b><p>20.Sep.2026</p></div>
</div></div>
</main></body></html>"""

DETAIL_NO_COUNTRY = """<html><body><main>
<h1>Some Role</h1><h2>KTH</h2>
<div class="job-details-content"><div class="details-main-col"><p>A description.</p></div>
<div class="details-secondary-col"><div class="details-list"><b>Location</b><p>Unknown Place</p></div></div>
</div></main></body></html>"""


class Offline(kth.KthConnector):
    pages = {}

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        body = self.pages[url].encode()
        res = FetchResult(url=url, final_url=url, status=200, content=body, content_type="text/html", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=200, content_type="text/html", robots_allowed=True)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_parse_feed_extracts_only_links_the_feeds_own_title_is_discovery_noise_only():
    links = kth.parse_feed(FEED)
    assert links == ["https://kth.se/lediga-jobb/963817", "https://kth.se/lediga-jobb/962556"]


def test_parse_detail_resolves_structured_english_labels():
    d = kth.parse_detail(DETAIL_DOCTORAL)
    assert d["title"] == "Doctoral student in acoustic source characterisation"
    assert d["city"] == "Stockholm" and d["country_iso2"] == "SE"
    assert d["ft_pt"] == "full"
    assert d["contract"] == "fixed"                                     # mapped to listing.contract's own enum
    assert d["contract_raw"] == "Temporary position"                     # the page's own text, kept alongside
    assert d["deadline"] == datetime(2026, 9, 24, tzinfo=timezone.utc)
    assert d["posted_at"] == datetime(2026, 9, 3, tzinfo=timezone.utc)
    assert d["department"] == "KTH Royal Institute of Technology, School of Engineering Sciences"
    assert "acoustic source characterisation" in d["body_text"]


def test_parse_detail_never_guesses_a_country_it_cannot_map():
    d = kth.parse_detail(DETAIL_NO_COUNTRY)
    assert d["country_iso2"] is None
    assert d["city"] == "Unknown Place"                              # city still resolved independently


def test_en_date_parser_is_locale_independent_not_strptime_pct_b():
    assert kth._parse_en_date("24.Sep.2026") == datetime(2026, 9, 24, tzinfo=timezone.utc)
    assert kth._parse_en_date("") is None
    assert kth._parse_en_date(None) is None
    assert kth._parse_en_date("not a date") is None


def test_contract_type_is_mapped_to_listings_own_controlled_enum_not_passed_through_as_free_text():
    """T21, found live (the first real ajp-run --once against a real seed): KTH's own free-text 'Type of
    employment' value was passed straight through to listing.contract, whose CHECK enum
    (fixed/permanent/grant/fellowship/phd_enrolment/service_contract) rejected 'Temporary position' outright
    with a real CheckViolation on ingest - the same bug class as inria.py's own, never caught by T19's own
    connector-level-only testing, which never ran a row through upsert_listing at all."""
    assert kth._CONTRACT_MAP.get("temporary position") == "fixed"
    assert kth._CONTRACT_MAP.get("permanent position") == "permanent"
    assert kth._CONTRACT_MAP.get("some future phrasing this connector has never seen") is None  # never guessed


def test_crawl_discards_hr_generalist_and_keeps_doctoral_student(tmp_path):
    Offline.pages = {
        kth.FEED_URL: FEED,
        "https://kth.se/lediga-jobb/963817": DETAIL_HR,
        "https://kth.se/lediga-jobb/962556": DETAIL_DOCTORAL,
    }
    c = Offline(fast(), LocalFsStore(tmp_path))
    links = c.discover()
    rows, report = c.crawl(links)
    assert report["attempted"] == 2 and report["parsed"] == 2 and report["kept"] == 1 and report["discarded"] == 1
    kept = rows[0]
    assert kept["title_raw"] == "Doctoral student in acoustic source characterisation"
    assert kept["kind"] == "employment" and kept["position_kind"] == "phd"
    assert kept["country_iso2"] == "SE" and kept["city"] == "Stockholm"
    assert kept["contact_emails"] == ["recruiter@kth.invalid"]                   # T26 (packet 015)
    assert kept["apply_url"] == "https://kth.se/lediga-jobb/962556"
    assert kept["ext_uid"] == "962556"
    assert kept["ad_lang"] == "en" and kept["org_raw"] == kth.ORG_RAW


def test_manifest_permits_both_the_bare_and_www_host_but_not_others(tmp_path):
    c = kth.KthConnector(fast(), LocalFsStore(tmp_path))
    assert c.manifest.permits(kth.FEED_URL)
    assert c.manifest.permits("https://kth.se/lediga-jobb/962556")
    assert c.manifest.permits("https://www.kth.se/lediga-jobb/962556")
    assert not c.manifest.permits("https://attacker.invalid/lediga-jobb/962556")


def test_manifest_never_permits_the_robots_disallowed_query_form(tmp_path):
    c = kth.KthConnector(fast(), LocalFsStore(tmp_path))
    assert not c.manifest.permits("https://www.kth.se/lediga-jobb?rss=1")
