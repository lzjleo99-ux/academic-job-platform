"""T20 acceptance: two-stage bounded career-path discovery (homepage keyword href harvest first, fixed-path
backfill second, capped at 12 fetched paths/org)."""
from datetime import datetime, timezone

from ajp import discovery
from ajp.connectors.base import FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

HOME_WITH_NAV_HIT = """<html><body>
<nav><a href="/o-fakultetu">O fakultetu</a><a href="/nastava">Nastava</a>
<a href="/konkursi">Konkursi</a></nav>
</body></html>"""

HOME_WITH_TEXT_ONLY_HIT = """<html><body>
<nav><a href="/en/node/1234">Careers</a><a href="/en/node/999">About</a></nav>
</body></html>"""

HOME_NO_HIT = """<html><body>
<nav><a href="/about">About</a><a href="/staff">Staff</a><a href="/students">Students</a></nav>
</body></html>"""

HOME_CROSS_DOMAIN_LINK = """<html><body>
<nav><a href="https://facebook.com/careers">Careers on Facebook</a><a href="/about">About</a></nav>
</body></html>"""


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


class Offline:
    """Patches PoliteClient.get via a fake fetch, keyed by exact URL - lets discover_career_paths run
    end-to-end against a scripted set of pages without a real network call. Any URL NOT in `pages` (e.g.
    the control probe's own nonce URL) 404s, unless `soft_404_body` is set, in which case every unlisted
    URL returns that SAME 200 body instead - simulating a real soft-404 host (rgf.bg.ac.rs, live-found)."""

    def __init__(self, pages, soft_404_body=None):
        self.pages = pages
        self.soft_404_body = soft_404_body
        self.fetched = []

    def __call__(self, client, store, org_slug, homepage, **kw):
        import ajp.discovery as mod
        orig = mod._Discoverer.fetch_raw

        def fake_fetch_raw(self, url, kind="html", max_bytes=None):
            if url not in self.pages_ref:
                if self.soft_404_body_ref is not None:
                    body = self.soft_404_body_ref.encode()
                    res = FetchResult(url=url, final_url=url, status=200, content=body, content_type="text/html", fetched_at=NOW)
                    ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body,
                                  fetched_at=NOW, status=200, content_type="text/html", robots_allowed=True)
                    return res, ref
                res = FetchResult(url=url, final_url=url, status=404, content=b"", content_type="text/html", fetched_at=NOW)
                ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=b"",
                              fetched_at=NOW, status=404, content_type="text/html", robots_allowed=True)
                return res, ref
            body = self.pages_ref[url].encode()
            res = FetchResult(url=url, final_url=url, status=200, content=body, content_type="text/html", fetched_at=NOW)
            ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                          status=200, content_type="text/html", robots_allowed=True)
            return res, ref

        mod._Discoverer.pages_ref = self.pages
        mod._Discoverer.soft_404_body_ref = self.soft_404_body
        mod._Discoverer.fetch_raw = fake_fetch_raw
        try:
            return discovery.discover_career_paths(client, store, org_slug, homepage, **kw)
        finally:
            mod._Discoverer.fetch_raw = orig


def test_href_harvest_finds_and_verifies_a_konkursi_nav_link(tmp_path):
    run = Offline({
        "https://org.invalid/": HOME_WITH_NAV_HIT,
        "https://org.invalid/konkursi": "<html><body>Konkursi ovde.</body></html>",
    })
    result = run(fast(), LocalFsStore(tmp_path), "org", "https://org.invalid/")
    assert result.method == "href_harvest"
    assert result.found_urls == ["https://org.invalid/konkursi"]
    assert result.paths_fetched == 1


def test_href_harvest_matches_on_anchor_text_when_the_href_itself_gives_no_clue():
    """A real, common shape: a CMS's own href is an opaque node id ('/en/node/1234'), only the anchor's own
    visible text ('Careers') says what the page is."""
    candidates = discovery._keyword_candidates(HOME_WITH_TEXT_ONLY_HIT, "https://org.invalid/")
    assert candidates == ["https://org.invalid/en/node/1234"]


def test_cross_domain_links_are_never_treated_as_this_orgs_own_career_page():
    candidates = discovery._keyword_candidates(HOME_CROSS_DOMAIN_LINK, "https://org.invalid/")
    assert candidates == []


def test_falls_back_to_fixed_path_backfill_when_the_nav_gives_no_hit(tmp_path):
    run = Offline({
        "https://org.invalid/": HOME_NO_HIT,
        "https://org.invalid/careers": "<html><body>Careers page.</body></html>",
    })
    result = run(fast(), LocalFsStore(tmp_path), "org", "https://org.invalid/")
    assert result.method == "fixed_path_backfill"
    assert result.found_urls == ["https://org.invalid/careers"]


def test_reports_none_found_when_neither_stage_yields_a_hit(tmp_path):
    """FIXED_PATHS itself has more than 12 entries, so a totally-unmatched org hits the fetch budget before
    exhausting the list - the cap always wins, never the full list length."""
    run = Offline({"https://org.invalid/": HOME_NO_HIT})
    result = run(fast(), LocalFsStore(tmp_path), "org", "https://org.invalid/")
    assert result.method == "none"
    assert result.found_urls == []
    assert result.paths_fetched == discovery.MAX_FETCHED_PATHS


def test_never_exceeds_the_fetched_path_budget(tmp_path):
    many_links = "".join(f'<a href="/konkurs-{i}">Konkurs {i}</a>' for i in range(20))
    run = Offline({"https://org.invalid/": f"<html><body>{many_links}</body></html>"})
    result = run(fast(), LocalFsStore(tmp_path), "org", "https://org.invalid/", max_fetched_paths=12)
    assert result.paths_fetched == 12


def test_reports_homepage_unreachable_without_raising(tmp_path):
    run = Offline({})                                                   # homepage itself 404s
    result = run(fast(), LocalFsStore(tmp_path), "org", "https://org.invalid/")
    assert result.method == "homepage_unreachable"
    assert result.found_urls == []


def test_a_soft_404_host_never_reports_a_false_positive_hit(tmp_path):
    """rgf.bg.ac.rs, live-found 2026-09-23: every path on this host, including a nonsense one, returns
    HTTP 200 with the byte-identical body - every fixed_path_backfill 'hit' would otherwise be fake."""
    fake_body = "<html><body>Not found (but served as 200).</body></html>"
    run = Offline({"https://org.invalid/": HOME_NO_HIT}, soft_404_body=fake_body)
    result = run(fast(), LocalFsStore(tmp_path), "org", "https://org.invalid/")
    assert result.method == "soft_404_host"
    assert result.found_urls == []


def test_a_soft_404_host_still_surfaces_a_genuinely_distinct_real_page(tmp_path):
    """The control-probe filter compares against the control's own fingerprint - a real, distinctly-sized
    page still counts, even on an otherwise soft-404 host."""
    fake_body = "<html><body>Not found (but served as 200).</body></html>"
    real_body = "<html><body>Real konkursi page with genuinely different content and length.</body></html>"
    run = Offline({"https://org.invalid/": HOME_WITH_NAV_HIT, "https://org.invalid/konkursi": real_body},
                  soft_404_body=fake_body)
    result = run(fast(), LocalFsStore(tmp_path), "org", "https://org.invalid/")
    assert result.method == "href_harvest"
    assert result.found_urls == ["https://org.invalid/konkursi"]


# ---------------------------------------------------------------- cross-domain homepage redirect (T20 PM correction) ---
def test_a_homepage_redirect_to_a_different_registrable_domain_is_resolved_not_abandoned(tmp_path):
    """T20 PM correction, live-found 2026-09-23: a real OpenAlex-listed org's own stated homepage
    (sportsacademy.edu.rs) genuinely redirects to a DIFFERENT registrable domain (www.spak.edu.rs) that IS
    still the same organisation. The original host-scoped-only manifest correctly refused to follow this
    blindly and gave up entirely (method='homepage_unreachable') - the real fix reads the org's OWN redirect
    back (same policy as ats_workday.discover_workday_site()) and widens the manifest by exactly that one
    confirmed host, never a guess, capped at MAX_REDIRECT_HOST_WIDENINGS. Uses a real PoliteClient over
    httpx.MockTransport (not the Offline test-double above, which bypasses manifest checking entirely and so
    could never exercise this path)."""
    import httpx

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "sportsacademy.edu.rs":
            return httpx.Response(301, headers={"location": "https://www.spak.edu.rs/"})
        if req.url.host == "www.spak.edu.rs" and req.url.path == "/":
            return httpx.Response(200, content=HOME_WITH_NAV_HIT.encode())
        if req.url.host == "www.spak.edu.rs" and req.url.path == "/konkursi":
            return httpx.Response(200, content=b"<html><body>Real konkursi page.</body></html>")
        return httpx.Response(404)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None)
    result = discovery.discover_career_paths(client, LocalFsStore(tmp_path), "sportsacademy",
                                              "https://sportsacademy.edu.rs/")
    assert result.method == "href_harvest"
    assert result.found_urls == ["https://www.spak.edu.rs/konkursi"]


def test_a_redirect_to_a_completely_unrelated_domain_with_no_real_content_still_gives_up_cleanly(tmp_path):
    """The widening only ever adds a host the org's OWN current server just redirected to - it does not turn
    into an unbounded crawl. A redirect chain longer than MAX_REDIRECT_HOST_WIDENINGS still fails cleanly
    (homepage_unreachable), never hangs or raises an unhandled exception."""
    import httpx

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        # each host redirects to yet another new host, forever - a pathological chain
        n = int(req.url.host.split(".")[0][1:]) if req.url.host.startswith("h") else 0
        return httpx.Response(301, headers={"location": f"https://h{n + 1}.invalid/"})

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None)
    result = discovery.discover_career_paths(client, LocalFsStore(tmp_path), "org", "https://h0.invalid/")
    assert result.method == "homepage_unreachable"
