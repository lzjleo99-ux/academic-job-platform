from datetime import datetime, timezone

from ajp.connectors import sljaka
from ajp.connectors.base import FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>DOMAR (1 izvršilac)</title><link>https://sljaka.com/poslovi/domar-1-izvrsilac/</link>
<pubDate>Tue, 22 Sep 2026 09:50:12 +0000</pubDate>
<content:encoded><![CDATA[<p>Institut za lečenje i rehabilitaciju Niška Banja raspisuje konkurs za prijem u radni odnos na određeno vreme za radno mesto DOMAR.</p>]]></content:encoded></item>
<item><title>Docent za oblast Informatika</title><link>https://sljaka.com/poslovi/docent-informatika/</link>
<pubDate>Tue, 22 Sep 2026 08:00:00 +0000</pubDate>
<content:encoded><![CDATA[<p>Fakultet organizacionih nauka raspisuje konkurs za izbor u zvanje docenta na odredjeno vreme.</p>
<p>Kontakt: <a href="mailto:konkurs@fon.invalid">konkurs@fon.invalid</a></p>]]></content:encoded></item>
<item><title>Konkurs za dodelu stipendija</title><link>https://sljaka.com/poslovi/stipendije/</link>
<pubDate>Tue, 22 Sep 2026 07:00:00 +0000</pubDate>
<content:encoded><![CDATA[<p>Otvoren je konkurs za dodelu stipendija za studente.</p>]]></content:encoded></item>
</channel></rss>"""


def test_parse_rss_extracts_title_link_pubdate_and_html_stripped_body():
    items = sljaka.parse_rss(RSS)
    assert len(items) == 3
    assert items[0]["title"] == "DOMAR (1 izvršilac)"
    assert "<p>" not in items[0]["body"] and "Institut za lečenje" in items[0]["body"]
    assert items[0]["posted"] == datetime(2026, 9, 22, 9, 50, 12, tzinfo=timezone.utc)


def test_classify_gates_employment_on_a_real_academic_position_noun():
    """The nsz.py pattern: a hospital janitor ad trips the bare 'radni odnos na odredjeno' employment marker
    the same way any bureaucratic hiring notice does, but 'DOMAR' is not an academic position - excluded. A
    real docent ad's title alone is often just the bare role name - the employment SHAPE ('raspisuje konkurs
    za izbor u zvanje') is only in the body, so the kind check needs both; the position-noun gate is title-only."""
    items = {i["title"]: i for i in sljaka.parse_rss(RSS)}
    assert sljaka.classify(items["DOMAR (1 izvršilac)"]["title"], items["DOMAR (1 izvršilac)"]["body"]) == "other"
    assert sljaka.classify(items["Docent za oblast Informatika"]["title"], items["Docent za oblast Informatika"]["body"]) == "employment"
    assert sljaka.classify(items["Konkurs za dodelu stipendija"]["title"], items["Konkurs za dodelu stipendija"]["body"]) == "grant_call"


class Offline(sljaka.SljakaConnector):
    pages = {}
    fetched = []

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        body = self.pages[url].encode()
        res = FetchResult(url=url, final_url=url, status=200, content=body, content_type="application/rss+xml", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=200, content_type="application/rss+xml", robots_allowed=True)
        Offline.fetched.append(url)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_crawl_discards_hospital_ad_keeps_docent_and_stipendija(tmp_path):
    Offline.pages = {sljaka.RSS_URL: RSS}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl()
    assert report == {"items": 3, "kept": 2, "discarded": 1}
    kinds = {r["title_raw"]: r["kind"] for r in rows}
    assert kinds == {"Docent za oblast Informatika": "employment", "Konkurs za dodelu stipendija": "grant_call"}
    for r in rows:
        assert r["country_iso2"] == "RS" and r["raw_html_ref"]
        assert r["location_class"] == "located"                       # T23 (PM ruling R2) completeness
    docent = next(r for r in rows if r["title_raw"] == "Docent za oblast Informatika")
    assert docent["contact_emails"] == ["konkurs@fon.invalid"]          # T26 (packet 015)
    assert docent["apply_url"] == "https://sljaka.com/poslovi/docent-informatika/"
