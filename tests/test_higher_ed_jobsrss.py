"""T27 (packet 015) acceptance: InsideHigherEd/Chronicle Jobs RSS connector, with trimmed real samples
captured live 2026-09-28 (both hosts run the identical underlying platform - one fixture set exercises both
configs, since the parsing logic and academic gate are shared code, not per-config behaviour)."""
from datetime import datetime, timezone

import pytest

from ajp.connectors import higher_ed_jobsrss as hej
from ajp.connectors.base import FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)

# Trimmed real sample from https://careers.insidehighered.com/jobsrss/ (captured live 2026-09-28) - one
# academic role (kept), one non-academic role (discarded), one with a structured trailing (XX) country code,
# one with a US-state-collision location (never guessed into the wrong country).
FEED = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
<title>Job Search RSS</title>
<item>
<title>University of North Carolina at Chapel Hill: Post-Doc Research Associate</title>
<description>


University of North Carolina at Chapel Hill:
Department:Mathematics - 318900Posting Open Date:09/28/2026Open Until Filled:YesPosition Type:Postdoctoral Scholar
Chapel Hill, NC
</description>
<link>https://careers.insidehighered.com/job/3556339/post-doc-research-associate/?TrackID=11</link>
<pubDate>Mon, 28 Sep 2026 04:00:00 -0500</pubDate>
</item>
<item>
<title>MOHAMMED VI POLYTECHNIC UNIVERSITY: SUSMAT-RC - Professor in Interfaces and Colloids</title>
<description>


MOHAMMED VI POLYTECHNIC UNIVERSITY:
The Sustainable Materials Research Center at UM6P invites applications for a faculty position in interfaces and colloids.
Benguerir, Morocco (MA)
</description>
<link>https://careers.insidehighered.com/job/3556343/susmat-rc-professor-in-interfaces-and-colloids/?TrackID=11</link>
<pubDate>Mon, 28 Sep 2026 08:23:00 -0500</pubDate>
</item>
<item>
<title>Georgia Institute of Technology: Research Cyber Infrastructure Engineer</title>
<description>


Georgia Institute of Technology:
Job Title: Research Cyber Infrastructure Engineer  Location: Atlanta, Georgia  Regular/Temporary: Regular
Atlanta, Georgia
</description>
<link>https://careers.insidehighered.com/job/3556368/research-cyber-infrastructure-engineer/?TrackID=11</link>
<pubDate>Mon, 28 Sep 2026 04:00:00 -0500</pubDate>
</item>
<item>
<title>University of North Carolina at Chapel Hill: Training Specialist</title>
<description>


University of North Carolina at Chapel Hill:
Department:Cont Impr Staff Dev Eng-215500Career Area :OtherPosting Open Date:09/28/2026
Chapel Hill, NC
</description>
<link>https://careers.insidehighered.com/job/3556338/training-specialist/?TrackID=11</link>
<pubDate>Mon, 28 Sep 2026 04:00:00 -0500</pubDate>
</item>
</channel></rss>"""


def test_f10_the_exact_real_pm_example_trackid_url_is_decoded():
    """T29/T29B (packet 017/019, PM's own exact named example): a real live item's own <link> literally
    contains '&amp;' for the query string's '&' (RSS/XML's own required escaping of a bare '&')."""
    feed = ('<?xml version="1.0"?><rss version="2.0"><channel><item>'
           '<title>Example U: Assistant Professor</title>'
           '<link>https://careers.insidehighered.com/job/3556493/assistant-professor/?TrackID=11&amp;utm_source=rss&amp;utm_medium=feed</link>'
           '</item></channel></rss>')
    items = hej.parse_feed_items(feed)
    assert items[0]["link"] == "https://careers.insidehighered.com/job/3556493/assistant-professor/?TrackID=11&utm_source=rss&utm_medium=feed"


def test_parse_feed_items_splits_employer_from_title_and_captures_the_trailing_location_line():
    items = {i["title"]: i for i in hej.parse_feed_items(FEED)}
    postdoc = items["Post-Doc Research Associate"]
    assert postdoc["employer"] == "University of North Carolina at Chapel Hill"
    assert postdoc["location_line"] == "Chapel Hill, NC"
    assert postdoc["link"] == "https://careers.insidehighered.com/job/3556339/post-doc-research-associate/?TrackID=11"
    assert postdoc["posted"] == datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)   # -0500 -> UTC


def test_a_title_with_no_colon_at_all_is_never_misparsed():
    items = hej.parse_feed_items(
        '<item><title>Postdoctoral Fellow</title><link>https://x.invalid/1</link></item>')
    assert items[0]["title"] == "Postdoctoral Fellow" and items[0]["employer"] is None


@pytest.mark.parametrize("line,expected", [
    ("Benguerir, Morocco (MA)", "MA"),                    # a real, stated structured ISO2 code
    ("Houston, Texas", "US"),
    ("Atlanta, Georgia", None),                            # the real US-state/country-name collision guard
    # 'NC' is ALSO New Caledonia's real ISO2 code (found live building this connector, the exact same
    # collision class as the project's own existing MD/DE/GA/... guard list) - a real live item
    # ('Chapel Hill, NC') must never be guessed into either country.
    ("Chapel Hill, NC", None),
])
def test_country_from_location_line(line, expected):
    assert hej.country_from_location_line(line) == expected


class Offline(hej.HigherEdJobsRssConnector):
    pages = {}

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        body = self.pages[url].encode()
        res = FetchResult(url=url, final_url=url, status=200, content=body, content_type="text/xml", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=200, content_type="text/xml", robots_allowed=True)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_crawl_keeps_academic_roles_discards_non_academic_never_guesses_ambiguous_country(tmp_path):
    Offline.pages = {hej.INSIDE_HIGHER_ED.feed_url: FEED}
    c = Offline(fast(), LocalFsStore(tmp_path), hej.INSIDE_HIGHER_ED)
    rows, report = c.crawl()
    assert report["attempted"] == 4
    by_title = {r["title_raw"]: r for r in rows}
    assert "Post-Doc Research Associate" in by_title and "SUSMAT-RC - Professor in Interfaces and Colloids" in by_title
    assert "Research Cyber Infrastructure Engineer" not in by_title    # non-academic, discarded
    assert "Training Specialist" not in by_title                       # non-academic, discarded

    postdoc = by_title["Post-Doc Research Associate"]
    # 'Chapel Hill, NC' is never guessed (NC collides with New Caledonia's own real ISO2 code) - the row is
    # still persisted, country_iso2=None + a real location_class (T23, PM ruling R2), never discarded.
    assert postdoc["country_iso2"] is None and postdoc["org_raw"] == "University of North Carolina at Chapel Hill"
    assert postdoc["position_kind"] == "postdoc" and postdoc["kind"] == "employment"
    assert postdoc["apply_url"] == postdoc["url"]
    assert postdoc["location_class"] == "unknown"

    professor = by_title["SUSMAT-RC - Professor in Interfaces and Colloids"]
    assert professor["country_iso2"] == "MA"                           # the real, structured (MA) code
    assert professor["org_raw"] == "MOHAMMED VI POLYTECHNIC UNIVERSITY"


def test_ensure_source_persists_config_for_both_real_instances(conn):
    for config in (hej.INSIDE_HIGHER_ED, hej.CHRONICLE_JOBS):
        hej.HigherEdJobsRssConnector.ensure_source(conn, config)
    ids = {r[0] for r in conn.execute("SELECT id FROM source").fetchall()}
    assert {"insidehighered", "chronicle_jobs"} <= ids


def test_manifest_permits_the_feed_and_job_paths_but_not_other_hosts():
    c_manifest_instance = hej.HigherEdJobsRssConnector(fast(), LocalFsStore("/tmp"), hej.INSIDE_HIGHER_ED)
    assert c_manifest_instance.manifest.permits(hej.INSIDE_HIGHER_ED.feed_url)
    assert c_manifest_instance.manifest.permits("https://careers.insidehighered.com/job/1/x/")
    assert not c_manifest_instance.manifest.permits("https://careers.insidehighered.com/analytics/")
    assert not c_manifest_instance.manifest.permits("https://jobs.chronicle.com/job/1/x/")
