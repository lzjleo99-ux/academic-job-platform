"""T19 acceptance: University of Copenhagen (ku_dk) with a recorded fixture (trimmed real sample from
employment.ku.dk/all-vacancies/?get_rss=1, captured 2026-09-23) exercising the malformed-link normalization
and the no-detail-fetch feed-content mapping."""
from datetime import datetime, timezone

from ajp.connectors import ku_dk
from ajp.connectors.base import FetchResult, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

# Trimmed real feed sample - byte-identical malformed <link>/<guid> shape (Obvius CMS stringified Perl hash
# ref glued onto the bare host), two items: one a real academic post, one a non-academic role.
FEED = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<title>Job vacancies - University of Copenhagen</title>
<language>en</language>
<item>
<title>166978 Clinical Professor of Innovative Technologies for Acute Cardiovascular Disease (fixed-term 5 years)</title>
<link>https://cms.ku.dkObvius::Document=HASH(0x557bc470a548)?show=166978</link>
<guid>https://cms.ku.dkObvius::Document=HASH(0x557bc470a548)?show=166978</guid>
<pubDate>Tue, 22 Sep 2026 00:00:00 +0000</pubDate>
<description><![CDATA[<p style="text-align:center"><strong>Clinical Professor of Innovative Technologies for Acute Cardiovascular Disease</strong></p>
<p><strong>Department of Clinical Medicine</strong><br />
<strong>Faculty of Health and Medical Sciences<br /> University of Copenhagen</strong></p>
<p>The University seeks to appoint a clinical professor of Innovative Technologies for Acute Cardiovascular Disease.</p>
<p>Contact: <a href="mailto:recruitment@ku.invalid">recruitment@ku.invalid</a></p>]]></description>
</item>
<item>
<title>Canteen Assistant</title>
<link>https://cms.ku.dkObvius::Document=HASH(0x557bc470a548)?show=168001</link>
<guid>https://cms.ku.dkObvius::Document=HASH(0x557bc470a548)?show=168001</guid>
<pubDate>Mon, 21 Sep 2026 00:00:00 +0000</pubDate>
<description><![CDATA[<p>The Faculty canteen is seeking a Canteen Assistant to support daily catering operations.</p>]]></description>
</item>
</channel></rss>"""

NO_SHOW_PARAM = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Broken Item</title><link>https://cms.ku.dkObvius::Document=HASH(0x0)</link>
<pubDate>Mon, 21 Sep 2026 00:00:00 +0000</pubDate><description><![CDATA[<p>No id recoverable.</p>]]></description></item>
</channel></rss>"""


class Offline(ku_dk.KuDkConnector):
    body = b""

    def fetch_raw(self, url, kind="xml", max_bytes=None):
        res = FetchResult(url=url, final_url=url, status=200, content=Offline.body, content_type="text/xml", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=Offline.body, fetched_at=NOW,
                      status=200, content_type="text/xml", robots_allowed=True)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_normalize_link_recovers_id_from_the_malformed_shows_param():
    ext_uid, url = ku_dk.normalize_link("https://cms.ku.dkObvius::Document=HASH(0x557bc470a548)?show=166978")
    assert ext_uid == "166978"
    assert url == "https://cms.ku.dk/?show=166978"


def test_normalize_link_never_guesses_when_no_show_param_is_present():
    assert ku_dk.normalize_link("https://cms.ku.dkObvius::Document=HASH(0x0)") == ("", "")


def test_parse_rss_recovers_both_items_via_the_link_not_the_title_prefix():
    items = ku_dk.parse_rss(FEED)
    assert len(items) == 2
    assert items[0]["ext_uid"] == "166978"
    assert items[0]["url"] == "https://cms.ku.dk/?show=166978"
    # 'Canteen Assistant' carries NO numeric prefix at all - proves the id must come from the link, not the title
    assert items[1]["ext_uid"] == "168001"


def test_parse_rss_skips_an_item_whose_link_carries_no_recoverable_id():
    assert ku_dk.parse_rss(NO_SHOW_PARAM) == []


def test_crawl_maps_the_academic_row_using_feed_content_only_no_detail_fetch(tmp_path):
    Offline.body = FEED.encode()
    c = Offline(fast(), LocalFsStore(tmp_path))
    items = c.discover()
    rows, report = c.crawl(items)
    assert report == {"attempted": 2, "kept": 1, "discarded": 1}
    prof = next(r for r in rows if r["ext_uid"] == "166978")
    assert prof["url"] == "https://cms.ku.dk/?show=166978"
    assert prof["org_raw"] == "University of Copenhagen"
    assert prof["country_iso2"] == "DK" and prof["city"] is None
    assert prof["kind"] == "employment" and prof["position_kind"] == "professor"
    assert prof["ad_lang"] == "en" and prof["status"] == "open"
    assert "Department of Clinical Medicine" in prof["raw_text"]
    assert prof["raw_html_ref"]                                     # populated even with no detail fetch made
    # T26 (packet 015)
    assert prof["contact_emails"] == ["recruitment@ku.invalid"]
    assert prof["apply_url"] == "https://cms.ku.dk/?show=166978"


def test_crawl_discards_the_non_academic_canteen_row(tmp_path):
    Offline.body = FEED.encode()
    c = Offline(fast(), LocalFsStore(tmp_path))
    items = c.discover()
    rows, _ = c.crawl(items)
    assert not any(r["ext_uid"] == "168001" for r in rows)


def test_manifest_scoped_to_the_feed_url_only():
    c = ku_dk.KuDkConnector(fast(), LocalFsStore("/tmp"))
    assert c.manifest.permits(ku_dk.FEED_URL)
    assert not c.manifest.permits("https://employment.ku.dk/some-other-path")
