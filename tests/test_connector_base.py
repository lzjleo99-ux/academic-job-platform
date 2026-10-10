import time

import pytest

from ajp.connectors.base import (Connector, FetchFailed, ManifestViolation, PoliteClient, UrlManifest)
from ajp.rawstore import LocalFsStore, get_raw
from ajp.robots import RobotsDenied
from stubserver import FakeClock, Stub


@pytest.fixture()
def stub():
    s = Stub()
    yield s
    s.stop()


def polite(fc=None, **kw):
    kw.setdefault("rps", 1000)
    if fc:
        kw.update(clock=fc.clock, sleep=fc.sleep)
    else:
        kw.setdefault("sleep", lambda s: None)
    return PoliteClient(**kw)


# ---------------------------------------------------------------- robots gate ------------
def test_allowed_url_is_fetched_and_robots_fetched_once(stub):
    stub.robots("User-agent: *\nDisallow: /private/\n").route("/open")
    c = polite()
    for _ in range(3):
        assert c.get(stub.base + "/open").status == 200
    assert stub.paths() == ["/robots.txt", "/open", "/open", "/open"]


def test_disallowed_url_is_never_requested(stub):
    stub.robots("User-agent: *\nDisallow: /private/\n").route("/private/x", body=b"secret")
    with pytest.raises(RobotsDenied):
        polite().get(stub.base + "/private/x")
    assert stub.paths() == ["/robots.txt"]            # the page itself was never hit


def test_euraxess_style_rules_wildcards_and_longest_match(stub):
    # the same rule shapes that made EURAXESS /jobs/* unfetchable (packet 003 matrix)
    stub.robots("User-agent: *\nDisallow: /*?\nAllow: /jobs\nDisallow: /jobs/*\n")
    for p in ("/jobs", ):
        stub.route(p)
    c = polite()
    assert c.get(stub.base + "/jobs").status == 200
    for denied in ("/jobs/search", "/jobs/452917", "/jobs/search?f%5B0%5D=job_country%3A786", "/other?x=1"):
        with pytest.raises(RobotsDenied):
            c.get(stub.base + denied)
    assert stub.paths() == ["/robots.txt", "/jobs"]


@pytest.mark.parametrize("status,allowed", [(404, True), (410, True), (500, False), (503, False),
                                            (403, False), (401, False), (429, False)])
def test_robots_status_policy(stub, status, allowed):
    stub.robots(status=status).route("/page")
    c = polite()
    if allowed:
        assert c.get(stub.base + "/page").status == 200
    else:
        with pytest.raises(RobotsDenied):
            c.get(stub.base + "/page")
        assert "/page" not in stub.paths()


def test_unreachable_robots_means_deny_and_no_page_request():
    s = Stub(); base = s.base; s.stop()               # nothing listens on this port any more
    with pytest.raises(RobotsDenied):
        polite(timeout=1).get(base + "/page")


# ---------------------------------------------------------------- cloud-storage redirect robots (PM 2026-09-22) -
# jobs.ac.uk's sitemapindex.xml redirects to a SHARED, multi-tenant S3 endpoint whose own bucket-root robots.txt
# returns a generic provider error, not a per-tenant policy. Ruling: on a redirect hop reached from an origin
# that already explicitly allowed the route, a robots.txt response that is a known cloud-storage provider ERROR
# document with zero robots directives is treated as absent; everything else (a real directive body, or a deny
# on the resource itself rather than its robots.txt) stays exactly as before.
S3_ACCESS_DENIED = "<?xml version='1.0' encoding='UTF-8'?>\n<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>"
GCS_ERROR = "<?xml version='1.0' encoding='UTF-8'?>\n<Error><Code>AccessDenied</Code><Message>Access denied.</Message></Error>"


def test_s3_access_denied_robots_on_a_redirect_hop_is_treated_as_absent(stub):
    storage = Stub()
    try:
        storage.robots(S3_ACCESS_DENIED, status=403)
        storage.route("/data.xml", body=b"<urlset></urlset>")
        stub.robots("User-agent: *\nAllow: /\n").route(  # origin explicitly allows, THEN redirects to storage
            "/sitemapindex.xml", status=302, headers={"Location": storage.base + "/data.xml"})
        assert polite().get(stub.base + "/sitemapindex.xml").status == 200
    finally:
        storage.stop()


def test_gcs_error_xml_robots_on_a_redirect_hop_is_treated_as_absent(stub):
    storage = Stub()
    try:
        storage.robots(GCS_ERROR, status=403)
        storage.route("/data.xml", body=b"<urlset></urlset>")
        stub.robots("User-agent: *\nAllow: /\n").route(
            "/sitemapindex.xml", status=302, headers={"Location": storage.base + "/data.xml"})
        assert polite().get(stub.base + "/sitemapindex.xml").status == 200
    finally:
        storage.stop()


def test_real_robots_directive_body_at_403_on_a_redirect_hop_still_denies(stub):
    """A genuine (even if oddly-served-at-403) robots.txt with real directives is NEVER reinterpreted as absent -
    only a body with zero robots syntax that also matches a cloud-provider error schema qualifies."""
    storage = Stub()
    try:
        storage.robots("User-agent: *\nDisallow: /\n", status=403)   # a REAL directive body, not a provider error
        storage.route("/data.xml", body=b"<urlset></urlset>")
        stub.robots("User-agent: *\nAllow: /\n").route(
            "/sitemapindex.xml", status=302, headers={"Location": storage.base + "/data.xml"})
        with pytest.raises(RobotsDenied):
            polite().get(stub.base + "/sitemapindex.xml")
    finally:
        storage.stop()


def test_403_on_the_redirected_resource_itself_stays_a_real_denied_fetch(stub):
    """The cloud-storage-error interpretation only ever applies to ROBOTS.TXT on a redirect hop - a 403 on the
    actual target object is an ordinary failed fetch, never silently reinterpreted or retried away."""
    storage = Stub()
    try:
        storage.robots("")                                            # storage's own robots.txt: absent, allow
        storage.route("/data.xml", status=403, body=S3_ACCESS_DENIED.encode())  # the OBJECT itself denied
        stub.robots("User-agent: *\nAllow: /\n").route(
            "/sitemapindex.xml", status=302, headers={"Location": storage.base + "/data.xml"})
        assert polite().get(stub.base + "/sitemapindex.xml").status == 403     # a real 403, not bypassed
    finally:
        storage.stop()


def test_robots_ttl_refetch_and_stale_grace():
    s = Stub(); fc = FakeClock()
    try:
        s.route("/robots.txt", (200, {}, b"User-agent: *\nDisallow: /no/\n"), (500, {}, b""))
        s.route("/ok").route("/no/x")
        c = polite(fc)
        assert c.get(s.base + "/ok").status == 200
        fc.t += 3600
        c.get(s.base + "/ok")
        assert s.paths().count("/robots.txt") == 1                       # cached within 24h
        fc.t += 86400                                                   # >24h: refetch -> 500
        assert c.get(s.base + "/ok").status == 200                       # stale copy still governs
        with pytest.raises(RobotsDenied):
            c.get(s.base + "/no/x")                                      # ...including its Disallow
        assert s.paths().count("/robots.txt") == 2                       # not refetched on every call
        fc.t += 86400 * 2                                               # good copy older than 2*TTL
        with pytest.raises(RobotsDenied):
            c.get(s.base + "/ok")
    finally:
        s.stop()


# ---------------------------------------------------------------- manifest ---------------
def test_manifest_refuses_before_any_request(stub):
    stub.robots("")
    m = UrlManifest.exact(stub.base + "/a")
    c = polite()
    with pytest.raises(ManifestViolation):
        c.get(stub.base + "/b", manifest=m)
    with pytest.raises(ManifestViolation):
        c.get(stub.base + "/a?x=1", manifest=m)           # exact means exact, query included
    assert stub.hits == []                                # not even robots.txt was requested
    stub.route("/a")
    assert c.get(stub.base + "/a", manifest=m).status == 200


def test_manifest_regex_patterns():
    m = UrlManifest(r"https://example\.org/konkursi/\d+", r"https://example\.org/oglasi/?")
    assert m.permits("https://example.org/konkursi/12") and m.permits("https://example.org/oglasi/")
    assert not m.permits("https://example.org/konkursi/12/edit") and not m.permits("https://evil.org/konkursi/1")
    with pytest.raises(ValueError):
        UrlManifest()


# ---------------------------------------------------------------- redirects --------------
def test_redirect_to_disallowed_url_is_not_followed(stub):
    stub.robots("User-agent: *\nDisallow: /private/\n")
    stub.route("/go", status=302, headers={"Location": "/private/x"}).route("/private/x")
    with pytest.raises(RobotsDenied):
        polite().get(stub.base + "/go")
    assert "/private/x" not in stub.paths()


def test_redirect_outside_manifest_is_refused_and_allowed_redirect_followed(stub):
    stub.robots("")
    stub.route("/old", status=301, headers={"Location": "/new"}).route("/new").route("/away", status=302, headers={"Location": "/elsewhere"})
    stub.route("/elsewhere")
    m = UrlManifest.exact(stub.base + "/old", stub.base + "/new", stub.base + "/away")
    c = polite()
    r = c.get(stub.base + "/old", manifest=m)
    assert r.status == 200 and r.final_url == stub.base + "/new" and r.url == stub.base + "/old"
    with pytest.raises(ManifestViolation):
        c.get(stub.base + "/away", manifest=m)
    assert "/elsewhere" not in stub.paths()


def test_redirect_loop_gives_up(stub):
    stub.robots("").route("/a", status=302, headers={"Location": "/a"})
    with pytest.raises(FetchFailed, match="too many redirects"):
        polite().get(stub.base + "/a")


# ---------------------------------------------------------------- headers ----------------
def test_user_agent_names_project_and_uses_configured_contact(stub, monkeypatch):
    stub.robots("").route("/p")
    monkeypatch.delenv("AJP_CONTACT_EMAIL", raising=False)
    polite().get(stub.base + "/p")
    ua = stub.hits[-1][1]["User-Agent"]
    assert "ajp-research-bot" in ua and "contact-not-configured@example.invalid" in ua and ua.startswith("Mozilla/5.0")
    assert "lzjleo99" not in ua and stub.hits[0][1]["User-Agent"] == ua      # robots.txt fetch too
    monkeypatch.setenv("AJP_CONTACT_EMAIL", "ops@example.org")
    polite().get(stub.base + "/p")
    assert "ops@example.org" in stub.hits[-1][1]["User-Agent"]


# ---------------------------------------------------------------- rate limit -------------
def test_rate_limit_real_clock(stub):
    stub.robots("").route("/p")
    c = PoliteClient(rps=10)                               # 100 ms between requests per host
    for _ in range(3):
        c.get(stub.base + "/p")
    ts = [h[2] for h in stub.hits]                         # robots + 3 pages
    assert len(ts) == 4 and all(b - a >= 0.085 for a, b in zip(ts, ts[1:]))


def test_rate_limit_is_per_host_and_default_is_one_rps(stub):
    stub.robots("").route("/p")
    fc = FakeClock()
    c = PoliteClient(client=None, clock=fc.clock, sleep=fc.sleep)      # default rps=1
    c.get(stub.base + "/p"); c.get(stub.base + "/p")
    assert fc.sleeps == [1.0, 1.0]                          # robots -> page 1 -> page 2, 1s apart
    other = stub.base.replace("127.0.0.1", "localhost")
    fc.sleeps.clear()
    c.get(other + "/p")                                     # different host: robots+page, one wait between
    assert fc.sleeps == [1.0]


def test_crawl_delay_directive_slows_the_client_beyond_default_rps(stub):
    """AcademicTransfer/polj.uns.ac.rs: robots.txt states 'Crawl-delay: 10' - honoured even with a fast default rps."""
    stub.robots("User-agent: *\nCrawl-delay: 5\n").route("/p")
    fc = FakeClock()
    c = PoliteClient(rps=1000, client=None, clock=fc.clock, sleep=fc.sleep)   # a fast default the delay must override
    c.get(stub.base + "/p")
    c.get(stub.base + "/p")
    assert 5.0 in fc.sleeps and all(s in (0.0, 5.0) or s < 0.01 for s in fc.sleeps)


def test_crawl_delay_is_capped_at_30_seconds(stub):
    stub.robots("User-agent: *\nCrawl-delay: 9999\n").route("/p")
    fc = FakeClock()
    c = PoliteClient(rps=1000, client=None, clock=fc.clock, sleep=fc.sleep)
    c.get(stub.base + "/p")
    c.get(stub.base + "/p")
    assert 30.0 in fc.sleeps and 9999.0 not in fc.sleeps


def test_robots_absent_or_silent_on_crawl_delay_keeps_the_default(stub):
    stub.robots("User-agent: *\nDisallow: /private/\n").route("/p")   # no Crawl-delay directive at all
    fc = FakeClock()
    c = PoliteClient(rps=10, client=None, clock=fc.clock, sleep=fc.sleep)
    c.get(stub.base + "/p")
    c.get(stub.base + "/p")
    assert fc.sleeps == [0.1, 0.1]                          # plain 1/rps spacing, nothing widened


def test_host_crawl_delay_override_takes_precedence_and_is_capped_too():
    """A caller-supplied override (e.g. a value already confirmed live, robots unreachable this run) is honoured
    directly, without needing a robots.txt fetch, and is capped the same way a parsed value is."""
    c = PoliteClient(rps=1, host_crawl_delay={"api.example.invalid": 12, "over.example.invalid": 9999})
    assert c._host_interval["api.example.invalid"] == 12.0
    assert c._host_interval["over.example.invalid"] == 30.0


# ---------------------------------------------------------------- retries ----------------
def test_backoff_on_5xx_then_success(stub):
    fc = FakeClock()
    stub.robots("").route("/p", (503, {}, b""), (503, {}, b""), (200, {}, b"finally"))
    r = polite(fc, backoff_base=0.5).get(stub.base + "/p")
    assert r.status == 200 and r.content == b"finally"
    assert [s for s in fc.sleeps if s >= 0.1] == [0.5, 1.0]            # exponential
    assert stub.paths().count("/p") == 3


def test_gives_up_after_max_retries(stub):
    stub.robots("").route("/p", status=500)
    with pytest.raises(FetchFailed, match="4 attempts"):
        polite(FakeClock(), backoff_base=0.1).get(stub.base + "/p")
    assert stub.paths().count("/p") == 4                                # 1 try + 3 retries


def test_retry_after_honoured_and_capped(stub):
    fc = FakeClock()
    stub.robots("").route("/a", (429, {"Retry-After": "7"}, b""), (200, {}, b"ok"))
    polite(fc, backoff_base=0.5).get(stub.base + "/a")
    assert 7.0 in fc.sleeps
    fc2 = FakeClock()
    stub.route("/b", (429, {"Retry-After": "9999"}, b""), (200, {}, b"ok"))
    polite(fc2, backoff_base=0.5, backoff_cap=10).get(stub.base + "/b")
    assert 10 in fc2.sleeps and max(fc2.sleeps) == 10


def test_4xx_other_than_429_is_returned_not_retried(stub):
    stub.robots("")
    r = polite().get(stub.base + "/missing")
    assert r.status == 404 and stub.paths().count("/missing") == 1


# ---------------------------------------------------------------- connector + envelope ---
class Demo(Connector):
    source_id = "demo"
    kind = "scrape_html"


def test_connector_stores_raw_with_provenance_envelope(stub, tmp_path):
    stub.robots("").route("/ad/1", body="<h1>Истраживач</h1>".encode())
    Demo.manifest = UrlManifest.exact(stub.base + "/ad/1")
    store = LocalFsStore(tmp_path)
    res, ref = Demo(polite(), store).fetch_raw(stub.base + "/ad/1")
    body, meta = get_raw(store, ref.key)
    assert body == "<h1>Истраживач</h1>".encode()
    assert meta["source_id"] == "demo" and meta["robots_allowed"] is True and meta["http_status"] == 200
    assert meta["url"] == stub.base + "/ad/1" and meta["fetched_at"].endswith("+00:00")
    with pytest.raises(ManifestViolation):
        Demo(polite(), store).fetch_raw(stub.base + "/ad/2")


def test_channel_connectors_cannot_be_constructed():
    class Chan(Connector):
        source_id = "euraxess"
        kind = "channel"
    with pytest.raises(TypeError, match="deeplink channel"):
        Chan(polite(), None)


def test_robots_txt_redirects_are_followed_even_across_hosts(stub):
    other = Stub()
    try:
        other.route("/robots.txt", status=200, body=b"User-agent: *\nDisallow: /private/\n")
        stub.route("/robots.txt", status=301, headers={"Location": other.base + "/robots.txt"}).route("/ok").route("/private/x")
        c = polite()
        assert c.get(stub.base + "/ok").status == 200
        with pytest.raises(RobotsDenied):                         # rules from the redirect target apply
            c.get(stub.base + "/private/x")
        assert "/private/x" not in stub.paths()
    finally:
        other.stop()


def test_robots_redirect_loop_means_unavailable_so_deny(stub):
    stub.route("/robots.txt", status=302, headers={"Location": "/robots.txt"}).route("/ok")
    with pytest.raises(RobotsDenied):
        polite().get(stub.base + "/ok")
    assert "/ok" not in stub.paths()


# ---------------------------------------------------------------- POST (T18 workday cxs) -----
def test_post_sends_json_body_and_is_robots_gated(stub):
    stub.robots("").route("/api", status=200, headers={"Content-Type": "application/json"}, body=b'{"ok":true}')
    c = polite()
    manifest = UrlManifest.exact(stub.base + "/api")
    res = c.post(stub.base + "/api", {"limit": 5}, manifest=manifest)
    assert res.status == 200 and res.content == b'{"ok":true}'


def test_post_denied_by_manifest_never_reaches_the_server(stub):
    stub.robots("").route("/api", status=200, body=b"{}")
    c = polite()
    with pytest.raises(ManifestViolation):
        c.post(stub.base + "/api", {}, manifest=UrlManifest.exact(stub.base + "/other"))
    assert "/api" not in stub.paths()


def test_post_denied_by_robots_never_reaches_the_server(stub):
    stub.robots("User-agent: *\nDisallow: /api\n").route("/api", status=200, body=b"{}")
    c = polite()
    with pytest.raises(RobotsDenied):
        c.post(stub.base + "/api", {}, manifest=UrlManifest.exact(stub.base + "/api"))
    assert "/api" not in stub.paths()


def test_post_retries_5xx_then_succeeds():
    from stubserver import FakeClock
    fc = FakeClock()
    stub = Stub()
    try:
        stub.robots("").route("/api", (503, {}, b"try again"), (200, {"Content-Type": "application/json"}, b'{"ok":true}'))
        c = polite(fc)
        res = c.post(stub.base + "/api", {}, manifest=UrlManifest.exact(stub.base + "/api"))
        assert res.status == 200
    finally:
        stub.stop()


# ---------------------------------------------------------------- size cap ---------------
def test_max_bytes_aborts_oversized_bodies_and_declared_lengths(stub):
    from ajp.connectors.base import ResponseTooLarge
    stub.robots("").route("/big", body=b"x" * 5000).route("/small", body=b"x" * 50)
    c = polite()
    assert c.get(stub.base + "/small", max_bytes=100).content == b"x" * 50
    with pytest.raises(ResponseTooLarge):
        c.get(stub.base + "/big", max_bytes=1000)            # Content-Length says 5000: rejected before reading the body
    assert stub.paths().count("/big") == 1                    # and never retried


def test_max_bytes_streaming_path_handles_a_compressed_body_without_double_decoding():
    """Found live, 2026-09-23 (ajp.thesis.similarity.sources.oa_fulltext's first real fetch of a brotli-
    compressed PDF host): resp.iter_bytes() already transparently decompresses while streaming, so `buf`
    holds plain bytes - but reconstructing the returned Response with the ORIGINAL (still 'Content-Encoding:
    br') headers made it try to decode already-plain bytes as if they were still compressed, raising
    httpx.DecodingError. Every caller that passes max_bytes against a compressed response hits this, not
    just PDF fetches - a general PoliteClient bug, not something specific to any one connector."""
    import brotli
    import httpx
    plain = b"a real plain-text body, well under the cap" * 5
    compressed = brotli.compress(plain)

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=compressed, headers={"content-encoding": "br"})

    c = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None)
    res = c.get("https://x.invalid/paper.pdf", max_bytes=10_000)   # streaming path (max_bytes set)
    assert res.content == plain                                    # decoded exactly once, not double-decoded
    assert res.status == 200


def test_oversized_chunked_body_is_cut_off():
    from ajp.connectors.base import ResponseTooLarge
    import httpx
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=iter([b"a" * 400] * 10))            # no Content-Length: streamed
    c = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None)
    with pytest.raises(ResponseTooLarge):
        c.get("https://x.invalid/big", max_bytes=1000)


class _FakeClock:
    """A clock that jumps forward by `step` "seconds" every call, regardless of exactly how many times it
    gets called elsewhere first - simulates real elapsed time without an actual wall-clock sleep in a test."""
    def __init__(self, step: float = 50.0):
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def test_a_streaming_body_that_trickles_forever_is_cut_off_by_an_overall_deadline_not_just_a_per_chunk_one():
    """F8 (found live, 2026-09-28): httpx's own configured `timeout` only bounds the GAP between reads, never
    the TOTAL time to receive a full streamed body - a server sending data slowly enough that every
    individual chunk still arrives inside that gap can make _send()'s streaming loop run forever despite a
    real, configured per-request timeout. Reproduced live against academictransfer.com: two independent,
    freshly-started `ajp-run --once` processes both had to be SIGKILLed - SIGTERM was ignored by both (the
    clearest evidence of a genuine hang: a process merely sleeping between polite requests responds to a
    signal near-instantly, one blocked inside a socket read does not). Every chunk here technically arrives
    "fine" on its own; only the cumulative time exceeds the real, overall deadline now enforced."""
    import httpx
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=iter([b"a" * 10] * 20))     # no Content-Length: streamed, chunked
    # the deadline's own httpx.ReadTimeout is a real httpx.TransportError, so it goes through the SAME
    # retry/backoff path any other transport error already does, surfacing as FetchFailed once retries are
    # exhausted - not a special case.
    c = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000,
                     sleep=lambda s: None, clock=_FakeClock(step=50.0), timeout=30.0)
    from ajp.connectors.base import FetchFailed
    with pytest.raises(FetchFailed, match="streaming body exceeded the overall 30s deadline"):
        c.get("https://x.invalid/slow-trickle", max_bytes=100_000)


# ---------------------------------------------------------------- T23: a real default cap, not opt-in ---------
def test_get_enforces_the_default_max_bytes_cap_when_the_caller_passes_none_at_all():
    """T23: found live auditing every fetch site (2026-09-24) - only 2 call sites across the whole project
    ever passed max_bytes explicitly; every other one relied on get()'s OLD default of None, which meant
    genuinely unbounded. get()'s own default is now DEFAULT_MAX_BYTES (20MB) - a caller that never mentions
    max_bytes at all is still capped, not merely a caller who happens to opt in."""
    from ajp.connectors.base import DEFAULT_MAX_BYTES, ResponseTooLarge
    import httpx

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * (DEFAULT_MAX_BYTES + 1),
                              headers={"content-length": str(DEFAULT_MAX_BYTES + 1)})

    c = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None)
    with pytest.raises(ResponseTooLarge):
        c.get("https://x.invalid/huge")                    # max_bytes not passed at all - the default must still apply


def test_fetch_raw_enforces_the_default_max_bytes_cap_too(tmp_path):
    """Same T23 default, one level up - Connector.fetch_raw() (what every real connector actually calls,
    never PoliteClient.get() directly) must forward the same real default, not silently re-widen it back to
    unbounded by passing its own bare None through."""
    from ajp.connectors.base import Connector, DEFAULT_MAX_BYTES, ResponseTooLarge, UrlManifest
    from ajp.rawstore import LocalFsStore
    import httpx

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * (DEFAULT_MAX_BYTES + 1),
                              headers={"content-length": str(DEFAULT_MAX_BYTES + 1)})

    class Probe(Connector):
        source_id = "probe_max_bytes"
        kind = "scrape_html"
        manifest = UrlManifest(r"https://x\.invalid/.*")

    c = Probe(PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None),
             LocalFsStore(tmp_path))
    with pytest.raises(ResponseTooLarge):
        c.fetch_raw("https://x.invalid/huge")               # max_bytes not passed at all


def test_post_enforces_a_response_size_cap_too(stub):
    """T23: post() (ats_workday_cxs's own POST search endpoint) had no size cap AT ALL before this fix - not
    even an opt-in one, since the parameter didn't exist."""
    from ajp.connectors.base import ResponseTooLarge
    stub.robots("").route("/api", body=b'{"jobPostings": []}' + b" " * 5000)
    c = polite()
    with pytest.raises(ResponseTooLarge):
        c.post(stub.base + "/api", {}, max_bytes=100)


# ---------------------------------------------------------------- robots.txt that is really an HTML page ------
def test_html_200_robots_txt_is_treated_as_absent_not_as_rules(stub):
    """PM finding (rgf.bg.ac.rs): a custom router answers /robots.txt with its normal HTML page, status 200.
    That must be read as 'no robots file published' (R1: absent = allow), not parsed as if it were rules."""
    stub.route("/robots.txt", status=200, headers={"Content-Type": "text/html; charset=utf-8"},
               body=b"<!doctype html><html><body><base href='https://x.invalid/'><p>Site home</p></body></html>")
    stub.route("/konkursi")
    assert polite().get(stub.base + "/konkursi").status == 200


def test_html_looking_body_without_a_content_type_is_also_caught(stub):
    stub.route("/robots.txt", status=200, headers={}, body=b"<HTML><BODY>oops, this is the homepage</BODY></HTML>")
    stub.route("/ok")
    assert polite().get(stub.base + "/ok").status == 200


def test_real_plaintext_robots_txt_is_unaffected(stub):
    stub.robots("User-agent: *\nDisallow: /private/\n").route("/ok").route("/private/x")
    c = polite()
    assert c.get(stub.base + "/ok").status == 200
    with pytest.raises(RobotsDenied):
        c.get(stub.base + "/private/x")


# ---------------------------------------------------------------- robots.txt redirect recursion (T20) ---------
def test_a_robots_txt_redirect_to_a_non_robots_path_does_not_recurse_forever():
    """Found live, 2026-09-23 (T20, a real host - beopolis.edu.rs's own /robots.txt): _send() always
    throttles first, throttling asks crawl_delay() what this host's robots.txt says, and crawl_delay()
    resolves robots.txt if it isn't cached yet. _effective_interval()'s own guard against this ("skip the
    crawl_delay lookup while fetching robots.txt itself") used to be a bare path=="/robots.txt" string
    check, which only caught the FIRST hop - a robots.txt redirect to ANY path not literally ending in
    "/robots.txt" (a domain-relocation redirect to an unrelated page, here) fell straight through it and
    recursed: _rules -> _fetch -> _send -> _throttle -> crawl_delay -> _rules (same origin, cache still
    empty) - RecursionError, not a slow leak. The real fix is the _resolving_robots re-entrancy flag (set
    for the WHOLE _fetch_robots() call, every hop), not a smarter path check."""
    import httpx

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(302, headers={"location": "https://beopolis.edu.rs/some/unrelated/page"})
        if req.url.path == "/some/unrelated/page":
            return httpx.Response(200, content=b"<html>not a robots policy</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"ok")

    c = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None)
    res = c.get("https://beopolis.edu.rs/")                    # must return normally, not raise RecursionError
    assert res.status == 200
