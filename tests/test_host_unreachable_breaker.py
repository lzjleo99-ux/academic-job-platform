"""F15: the per-host unreachability breaker.

Found live 2026-10-13 by profiling a real cycle with the now-working F2 durations. jobs_ac_uk's most
recent run recorded 10,834s - exactly the F9 3-hour per-source watchdog ceiling - having fetched ZERO
pages. The cause is not a slow site and not a hang: connectors catch FetchFailed PER URL and count it,
raising only at the end ("all N detail fetches failed"). So a host whose network is unreachable is
ground through its ENTIRE url list at full retry cost, and jobs_ac_uk has ~2,000 detail URLs at ~7s of
retries+backoff each.

The F9 watchdog bounds that at 3h, which is a floor, not a fix - and R1's three-strikes would need three
such runs (9 hours, 37% of a 24h cycle) before blocking the source. This breaker makes the FIRST failed
run cheap instead: three consecutive connection-level URL failures on a host and its remaining URLs are
not attempted at all.

Transport-only and per-host, both deliberate - see HostUnreachable's own docstring. The tests below pin
each of those scoping decisions, because getting either wrong is worse than having no breaker: a global
counter would let one dead site abort a whole multi-source cycle, and a status-code-fed counter would
silently skip a host that is merely rate-limiting us.
"""
import time

import httpx
import pytest

import ajp.connectors.base as base
from ajp.connectors.base import (
    MAX_CONSECUTIVE_HOST_FAILURES,
    FetchFailed,
    HostUnreachable,
    PoliteClient,
)


def _client(handler, **kw):
    kw.setdefault("rps", 1000)
    kw.setdefault("sleep", lambda s: None)
    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def _always_connect_error(request):
    """A host that refuses every connection - the sandbox shape that produced the 3-hour run."""
    if request.url.path == "/robots.txt":
        return httpx.Response(404)                 # R1: robots 404/410 == allowed, so fetching proceeds
    raise httpx.ConnectError("[Errno 61] Connection refused")


def _http_status(status):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(status)
    return handler


def test_three_consecutive_transport_failures_trip_the_breaker_and_stop_attempting_that_host():
    """The core behaviour. Each URL gets its own full retry budget (that is unchanged and correct), but
    once three URLs have exhausted theirs at the connection level the host is done: the next URL raises
    HostUnreachable WITHOUT the transport being touched at all. Asserted on the handler's call count, so
    "not attempted" is checked against the wire rather than inferred from the exception."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return _always_connect_error(request)

    c = _client(handler)
    urls = [f"https://dead.invalid/job/{i}" for i in range(10)]

    # The first MAX-1 URLs each exhaust their own retry budget and report an ordinary give-up - that
    # part is unchanged behaviour, and one or two failures must never look like a dead host.
    for u in urls[:MAX_CONSECUTIVE_HOST_FAILURES - 1]:
        with pytest.raises(FetchFailed, match="giving up"):
            c.get(u)

    # The MAX-th URL is the one that trips it: its own exhaustion raises HostUnreachable, not a plain
    # give-up, so a caller can tell "this host is gone" from "this URL failed".
    with pytest.raises(HostUnreachable, match="connection level"):
        c.get(urls[MAX_CONSECUTIVE_HOST_FAILURES - 1])
    attempts_at_trip = calls["n"]

    # Every URL after that fails BEFORE the transport is touched at all.
    with pytest.raises(HostUnreachable, match="not attempting"):
        c.get(urls[MAX_CONSECUTIVE_HOST_FAILURES])
    assert calls["n"] == attempts_at_trip, "the next URL must not have reached the transport at all"

    for u in urls[MAX_CONSECUTIVE_HOST_FAILURES + 1:]:
        with pytest.raises(HostUnreachable):
            c.get(u)
    assert calls["n"] == attempts_at_trip, "no further URL on a tripped host may be attempted"


def test_the_waste_it_prevents_is_real_and_measurable():
    """The jobs_ac_uk shape, as a number. ~2,000 detail URLs on an unreachable host: with the breaker
    the transport is touched a bounded number of times (3 URLs x their own retry budget + robots),
    not 2,000 x 4. This is the difference between a ~1-minute failed run and a 3-hour one."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return _always_connect_error(request)

    c = _client(handler)
    urls = [f"https://dead.invalid/job/{i}" for i in range(2000)]
    for u in urls:
        try:
            c.get(u)
        except FetchFailed:
            pass

    per_url_budget = c._max_retries + 1
    unbounded = len(urls) * per_url_budget
    assert calls["n"] < 50, f"transport touched {calls['n']} times; unbounded would be {unbounded}"
    assert calls["n"] <= MAX_CONSECUTIVE_HOST_FAILURES * per_url_budget + 1


def test_the_breaker_is_per_host_one_dead_site_never_stops_another():
    """Scoping, and the reason it is per-host rather than global. Two hosts, one refusing connections
    and one healthy: the healthy one must keep working after the dead one has tripped. A process-global
    counter would abort the rest of a multi-source cycle here, which is strictly worse than the bug."""
    def handler(request):
        host = request.url.host
        # the dead host's robots.txt must still RESOLVE (404 == allowed, R1) - otherwise the very first
        # get() dies on RobotsDenied before any data URL is ever attempted, and the test would be
        # exercising the robots path instead of the breaker.
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if host == "dead.invalid":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, content=b"fine")

    c = _client(handler)
    for i in range(MAX_CONSECUTIVE_HOST_FAILURES + 2):
        with pytest.raises(FetchFailed):
            c.get(f"https://dead.invalid/job/{i}")

    # the dead host is tripped...
    with pytest.raises(HostUnreachable):
        c.get("https://dead.invalid/another")
    # ...and the healthy host is entirely unaffected
    for i in range(5):
        assert c.get(f"https://alive.invalid/job/{i}").content == b"fine"


def test_an_http_error_status_never_feeds_the_breaker():
    """Transport-only. A host answering 503 is emphatically REACHABLE - it is telling us it is overloaded,
    which is a reason to back off, not to write it off. If status codes fed the breaker, a rate-limited or
    briefly-overloaded source would be silently skipped for the rest of the run and its real listings
    lost, which is a far worse failure than the one being fixed."""
    c = _client(_http_status(503))
    for i in range(MAX_CONSECUTIVE_HOST_FAILURES + 5):
        with pytest.raises(FetchFailed, match="HTTP 503"):
            c.get(f"https://busy.invalid/job/{i}")
    # never a HostUnreachable, and the host is still being attempted every single time
    assert c._host_url_failures.get("busy.invalid", 0) == 0


def test_one_successful_response_resets_the_counter():
    """Recovery. A flapping host (refuse, refuse, answer, refuse, refuse, ...) must never trip the breaker,
    or a transient blip would take a working source out of the whole run. Any HTTP response resets it."""
    state = {"i": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        state["i"] += 1
        if state["i"] % 3 == 0:
            return httpx.Response(200, content=b"ok")
        raise httpx.ConnectError("refused")

    c = _client(handler)
    for i in range(20):
        c.get(f"https://flap.invalid/job/{i}")        # never raises: 1-in-3 succeeds and resets
    assert "flap.invalid" not in c._host_url_failures


def test_one_or_two_transport_failures_do_not_trip_the_breaker():
    """The threshold is a floor, not a hair trigger. A single refused connection is an ordinary network
    event; only a sustained run of them means the host is gone. Tripping on one would make the client
    brittle against exactly the transient noise R1 already treats as transient."""
    c = _client(_always_connect_error)
    for i in range(MAX_CONSECUTIVE_HOST_FAILURES - 1):
        with pytest.raises(FetchFailed, match="giving up"):
            c.get(f"https://dead.invalid/job/{i}")
    assert "dead.invalid" in c._host_url_failures          # counted...
    assert c._host_url_failures["dead.invalid"] < MAX_CONSECUTIVE_HOST_FAILURES   # ...but not tripped


def test_host_unreachable_is_a_fetch_failed_subclass_so_existing_handlers_keep_working():
    """Compatibility. Every connector catches `except (FetchFailed, ManifestViolation, RobotsDenied)` per
    URL and counts the failure, then raises "all N detail fetches failed" at the end. HostUnreachable
    being a FetchFailed subclass means that whole path is unchanged - the breaker makes it FAST without
    any connector needing to know the breaker exists. If this ever stopped being true, every connector's
    error handling would have to be revisited at once."""
    assert issubclass(HostUnreachable, FetchFailed)
    c = _client(_always_connect_error)
    for i in range(MAX_CONSECUTIVE_HOST_FAILURES):
        with pytest.raises(FetchFailed):
            c.get(f"https://dead.invalid/job/{i}")
    with pytest.raises(FetchFailed):                        # the connector's existing handler
        c.get("https://dead.invalid/job/late")


def test_robots_txt_resolution_is_unaffected_by_the_breaker():
    """Scoping, the other edge. robots.txt is fetched through _fetch_robots, a different path from
    _request_with_retries. If the breaker reached it, a host whose robots.txt refused a connection would
    poison the counter for the host's data URLs before any data URL was ever tried - and a RobotsCache
    miss would look like host unreachability. Asserted by tripping the breaker on data URLs and then
    confirming robots resolution for a DIFFERENT host still works normally."""
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "dead.invalid":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, content=b"ok")

    c = _client(handler)
    for i in range(MAX_CONSECUTIVE_HOST_FAILURES + 1):
        with pytest.raises(FetchFailed):
            c.get(f"https://dead.invalid/job/{i}")
    with pytest.raises(HostUnreachable):
        c.get("https://dead.invalid/job/again")
    # a fresh host resolves robots and fetches fine, proving the breaker did not leak into that path
    assert c.get("https://fresh.invalid/job/1").content == b"ok"


# ---------------------------------------------------------------- against a real connector ----------------

def test_a_real_connectors_crawl_loop_stops_paying_for_an_unreachable_host(tmp_path):
    """The end-to-end measurement, on the connector whose live run this fixes.

    Connectors catch FetchFailed PER URL and only raise at the end ("all N detail fetches failed"), so the
    breaker has to make that LOOP cheap, not merely make the client stop trying. Run jobs_ac_uk's real
    crawl() over 500 detail URLs against a transport that refuses every connection, with the breaker ON
    and OFF, and compare both the wall clock and the number of requests that actually reached the wire.

    Measured with 2,000 URLs (the live cycle's real count) while developing this: 8,001 requests and
    0.71s with the breaker off, versus 13 requests and 0.02s with it on - a 46x reduction, with the
    connector's own final report byte-identical in both cases. On the live stack, where each refused
    connection costs a real timeout and politeness spacing, the OFF column is the 10,834s run that
    fetched zero pages.
    """
    from ajp.connectors.jobs_ac_uk import JobsAcUkConnector
    from ajp.rawstore import LocalFsStore

    urls = [f"https://www.jobs.ac.uk/job/DS{80000 + i}/an-academic-position-{i}/" for i in range(500)]
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("[Errno 61] Connection refused")

    def crawl_once():
        calls["n"] = 0
        client = _client(handler, rps=100000)          # rps neutralised: measure the breaker, not politeness
        conn = JobsAcUkConnector(client, LocalFsStore(tmp_path))
        t0 = time.monotonic()
        try:
            conn.crawl(urls)
        except FetchFailed:
            pass                                       # the connector's own honest "all N failed"
        return time.monotonic() - t0, calls["n"]

    off_elapsed, off_calls = crawl_once()

    # breaker off == the pre-F15 behaviour: a threshold no run of failures can ever reach
    original = base.MAX_CONSECUTIVE_HOST_FAILURES
    base.MAX_CONSECUTIVE_HOST_FAILURES = 10 ** 9
    try:
        off_elapsed, off_calls = crawl_once()
    finally:
        base.MAX_CONSECUTIVE_HOST_FAILURES = original

    on_elapsed, on_calls = crawl_once()

    assert off_calls == len(urls) * 4 + 1, f"pre-F15 should attempt every URL's full budget, got {off_calls}"
    assert on_calls <= MAX_CONSECUTIVE_HOST_FAILURES * 4 + 1, f"breaker should bound attempts, got {on_calls}"
    assert on_calls < off_calls / 10, "the breaker must remove at least 90% of the requests"
    assert on_elapsed < off_elapsed, "the crawl loop itself must get faster, not just the client"
