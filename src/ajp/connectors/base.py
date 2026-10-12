"""Connector governance base (T12 pulled forward, packet 003 part A).

Every outbound request from a connector goes through ``PoliteClient.get`` which, in order:
  1. refuses URLs outside the connector's permitted-URL manifest (``ManifestViolation``),
  2. refuses URLs robots.txt disallows or cannot vouch for (``RobotsDenied``),
  3. waits for the per-host rate limit (default 1 request/second),
  4. sends the request over verified TLS (certifi), retrying 429/5xx/transport errors with
     exponential backoff (Retry-After honoured, capped) up to ``max_retries``,
  5. follows redirects manually, re-running 1-3 for every hop.
``Connector.fetch_raw`` then stores the body with a provenance envelope
(source, url, fetched_at, http_status, robots_allowed=true) that ingestion requires.
"""
from __future__ import annotations

import logging
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, Optional, Pattern, Tuple
from urllib.parse import urljoin, urlsplit

import certifi
import httpx

from ajp.config import user_agent as default_user_agent
from ajp.rawstore import ObjectStore, RawRef, put_raw
from ajp.robots import RobotsCache, RobotsDenied

log = logging.getLogger("ajp.connectors")

MAX_REDIRECTS = 5
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_CRAWL_DELAY_SECONDS = 30.0   # a site's own robots.txt Crawl-delay is honoured up to this cap, never beyond it
# F15 (found live 2026-10-13): consecutive URLs on ONE host that each exhausted their own retries with a
# connection-level transport error before the host is treated as unreachable. See HostUnreachable.
MAX_CONSECUTIVE_HOST_FAILURES = 3
# T23: response size cap. Found live auditing every fetch_raw()/get()/post() call site (2026-09-24) - only
# 2 of them (nsz.py's own 25MB PDF cap, board.py's deadline-page cap) ever passed max_bytes explicitly;
# every other HTML/JSON/XML detail-page, feed and sitemap fetch across the whole project had NO cap at all
# (max_bytes=None meant genuinely unbounded, not "use a sane default"). 20MB is generous for any real
# HTML/JSON/XML page this project fetches (the largest observed, a PDF issue, already has its own explicit,
# larger cap) while still bounding a misbehaving or malicious server's response before it is ever buffered
# into memory or written to the raw store.
DEFAULT_MAX_BYTES = 20 * 1024 * 1024


def _looks_like_robots_txt(content_type: str, body: str) -> bool:
    """A 200 response is only a robots policy if it plausibly IS one. Some sites route every unknown path,
    /robots.txt included, to their normal HTML page; that page is not a set of rules to parse."""
    if content_type and "html" in content_type.lower():
        return False
    return not body.lstrip()[:200].lower().startswith(("<!doctype", "<html", "<?xml"))


class ManifestViolation(PermissionError):
    """``url``, when the caller supplies it, is the exact URL the manifest refused - lets a caller that
    wants to know WHERE a blocked redirect was heading (e.g. discovery.py's own cross-domain-redirect
    detection) read it back structurally, instead of parsing it out of the message string. Optional and
    keyword-only so every existing call site (many pass a bare URL, or a message unrelated to any single
    URL, as the positional arg) keeps working unchanged; .url simply stays None for those."""

    def __init__(self, message: object, *, url: Optional[str] = None):
        super().__init__(message)
        self.url = url


class FetchFailed(RuntimeError):
    pass


def is_transport_error(exc: BaseException) -> bool:
    """True if a FetchFailed was caused by a connection-level error (TLS handshake, refused, reset, timeout)."""
    return isinstance(exc.__cause__, httpx.TransportError)


class ResponseTooLarge(FetchFailed):
    """Body larger than the caller's max_bytes; never retried (retrying cannot make it smaller)."""


class HostUnreachable(FetchFailed):
    """Raised once MAX_CONSECUTIVE_HOST_FAILURES URLs on one host have each exhausted their own retries
    with a connection-level transport error (refused / reset / timeout / TLS), and from then on for every
    further URL on that host, BEFORE any network attempt or backoff sleep.

    Why it exists, measured on the live stack 2026-10-13: connectors catch FetchFailed PER URL and count
    it, raising only at the end ("all N detail fetches failed"). So a host whose network is unreachable
    gets ground through its ENTIRE url list at full retry cost. jobs_ac_uk's most recent run burned
    10,834s - exactly the F9 3-hour per-source watchdog ceiling - having fetched ZERO pages, because its
    ~2,000 detail URLs each cost ~7s of retries+backoff before being counted and discarded. With the
    breaker the same run fails in seconds and says why.

    A subclass of FetchFailed so every existing `except FetchFailed` handler keeps working unchanged; the
    distinction is for the log and for a caller that wants to tell "this host is down" (nothing to gain
    from its remaining URLs) from "this one URL failed".

    Two scoping decisions, both deliberate:
      * TRANSPORT-ONLY. A 403/429/5xx means the host ANSWERED, so it resets the counter rather than
        feeding it - a rate-limited or forbidden host is emphatically reachable, and treating it as
        unreachable would silently skip a source that is merely being polite back at us.
      * PER-HOST. One dead site must never trip the breaker for any other host, so a single unreachable
        source cannot abort a whole multi-source cycle the way a process-global counter would.
    """

    def __init__(self, message: object, *, url: Optional[str] = None):
        # Same shape as ManifestViolation above (FetchFailed itself is a bare RuntimeError subclass and
        # takes no keyword), so .url is readable structurally rather than parsed back out of the message.
        super().__init__(message)
        self.url = url


class UrlManifest:
    """Allow-list of URLs a connector may request. Patterns are regexes that must match the
    whole URL (``re.fullmatch``), so an unlisted path or query is refused, not just a host."""

    def __init__(self, *patterns: str):
        if not patterns:
            raise ValueError("a manifest needs at least one pattern")
        self.patterns = tuple(patterns)
        self._compiled: Tuple[Pattern[str], ...] = tuple(re.compile(p) for p in patterns)

    @classmethod
    def exact(cls, *urls: str) -> "UrlManifest":
        return cls(*(re.escape(u) for u in urls))

    def permits(self, url: str) -> bool:
        return any(p.fullmatch(url) for p in self._compiled)


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    content: bytes
    content_type: Optional[str]
    fetched_at: datetime
    robots_allowed: bool = True  # always true: a robots-denied URL is never requested


class PoliteClient:
    def __init__(
        self,
        *,
        user_agent: Optional[str] = None,
        rps: float = 1.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_cap: float = 60.0,
        timeout: float = 30.0,
        robots_ttl: float = 86400.0,
        client: Optional[httpx.Client] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        host_crawl_delay: Optional[Dict[str, float]] = None,
    ):
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.user_agent = user_agent or default_user_agent()
        self._interval = 1.0 / rps
        self._max_retries, self._base, self._cap = max_retries, backoff_base, backoff_cap
        self._clock, self._sleep = clock, sleep
        self._last: Dict[str, float] = {}
        # per-host effective spacing: max(1/rps default, the site's own declared robots.txt Crawl-delay, capped).
        # A UrlManifest/caller-supplied override (host_crawl_delay) takes precedence and is never overwritten by
        # the parsed value - both live in the same dict, since once resolved a host's spacing never changes again.
        self._host_interval: Dict[str, float] = {h: max(self._interval, min(d, MAX_CRAWL_DELAY_SECONDS))
                                                 for h, d in (host_crawl_delay or {}).items()}
        # Re-entrancy guard: True while _fetch_robots() is resolving a robots.txt fetch itself, including
        # every redirect hop it follows. Found live 2026-09-23 (RecursionError, a real host - beopolis.edu.rs
        # - whose /robots.txt redirects to a URL not literally ending in "/robots.txt"): _send() always
        # throttles first, throttling always asks crawl_delay() what THIS host's robots.txt says, and
        # crawl_delay() resolves robots.txt if it isn't cached yet - a genuine infinite recursion whenever a
        # robots.txt redirect hop's own URL doesn't happen to end in "/robots.txt" (the previous guard below,
        # a path=="/robots.txt" string check, only ever caught the FIRST hop; any later hop, or a redirect to
        # a different path/host entirely, fell straight through it). While this flag is set, every throttle
        # on ANY host uses the plain default interval - correct, since asking robots.txt to rate-limit
        # fetching robots.txt is circular by definition, exactly the case the original guard already existed
        # to prevent, just too narrowly.
        self._resolving_robots = False
        # F15: consecutive URLs per host that exhausted their retries at the connection level. Keyed by
        # netloc, the same host identity _throttle already uses, so a breaker and a rate limit always
        # agree about what "one host" means.
        self._host_url_failures: Dict[str, int] = {}
        self._timeout = timeout
        # TLS certificate verification is always on (certifi bundle); there is no opt-out.
        self._client = client or httpx.Client(timeout=timeout, verify=ssl.create_default_context(cafile=certifi.where()))
        self._robots = RobotsCache(self._fetch_robots, ttl=robots_ttl, clock=clock)

    # -- plumbing ---------------------------------------------------------------------------
    def _effective_interval(self, url: str, host: str) -> float:
        cached = self._host_interval.get(host)
        if cached is not None:
            return cached
        if self._resolving_robots or urlsplit(url).path == "/robots.txt":
            # fetching robots.txt itself, for THIS host, for the first time - its own Crawl-delay directive
            # cannot be known yet (that would mean asking robots.txt to rate-limit fetching robots.txt), so this
            # one request uses the plain default; the real delay applies from the next request on this host.
            # The _resolving_robots flag (not just this path check) is what actually makes this safe across
            # every redirect hop _fetch_robots() follows, not only its first one - see __init__'s own note.
            return self._interval
        declared = self._robots.crawl_delay(url, self.user_agent)  # already-cached robots rules, no extra fetch
        interval = self._interval if declared is None else max(self._interval, min(float(declared), MAX_CRAWL_DELAY_SECONDS))
        if declared is not None and declared > MAX_CRAWL_DELAY_SECONDS:
            log.warning("Crawl-delay %.0fs for %s exceeds the %.0fs cap; capped", declared, host, MAX_CRAWL_DELAY_SECONDS)
        self._host_interval[host] = interval
        return interval

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        interval = self._effective_interval(url, host)
        last = self._last.get(host)
        if last is not None:
            wait = interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._clock()

    def _send(self, url: str, max_bytes: Optional[int] = None, extra_headers: Optional[Dict[str, str]] = None) -> httpx.Response:
        self._throttle(url)
        headers = {"User-Agent": self.user_agent, "Accept-Language": "en", **(extra_headers or {})}
        if max_bytes is None:
            r = self._client.get(url, headers=headers, follow_redirects=False)
        else:   # stream so an oversized body is abandoned as soon as the cap is exceeded
            with self._client.stream("GET", url, headers=headers, follow_redirects=False) as resp:
                declared = resp.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise ResponseTooLarge(f"{url}: Content-Length {declared} exceeds cap {max_bytes}")
                buf = bytearray()
                # F8 (found live, 2026-09-28): httpx's own `timeout` only bounds the gap BETWEEN reads, never
                # the TOTAL time to receive a streamed body - a server trickling data with each chunk arriving
                # just under that gap apart can make this loop run indefinitely despite a "30s timeout"
                # configured. Reproduced live against academictransfer.com: two independent, fresh `ajp-run
                # --once` processes both stalled here (real evidence: SIGTERM was ignored by both, needing
                # SIGKILL - a process merely sleeping between polite requests responds to a signal near-
                # instantly, one blocked inside an httpx socket read does not). A real overall deadline, not
                # just a per-chunk one, is enforced here so a single slow-trickling URL can never hang the
                # whole scheduler - raises the same exception class the retry/backoff logic already handles.
                deadline = self._clock() + self._timeout
                for chunk in resp.iter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise ResponseTooLarge(f"{url}: body exceeds cap {max_bytes}")
                    if self._clock() > deadline:
                        raise httpx.ReadTimeout(
                            f"{url}: streaming body exceeded the overall {self._timeout:.0f}s deadline "
                            f"(received {len(buf)} bytes so far)", request=resp.request)
                # resp.iter_bytes() already transparently decompresses (gzip/br/deflate) - buf holds DECODED
                # bytes. The original headers still declare that encoding though, so passing them straight
                # through to a new Response (which eagerly re-reads/re-decodes any `content=` bytes it is
                # given) tries to brotli/gzip-decode already-plain bytes and fails outright. Found live,
                # 2026-09-23, against a real brotli-compressed PDF host - the first caller in this project to
                # combine max_bytes streaming with a compressed response. Content-Length is dropped too since
                # it described the original (possibly still-compressed) transfer size, not len(buf).
                headers = httpx.Headers(resp.headers)
                for stale in ("content-encoding", "content-length"):
                    headers.pop(stale, None)
                r = httpx.Response(resp.status_code, headers=headers, content=bytes(buf), request=resp.request)
        self._last[urlsplit(url).netloc] = self._clock()
        return r

    def _fetch_robots(self, origin: str) -> Tuple[int, str]:
        # RFC 9309 2.3.1.2: follow up to 5 redirects for robots.txt, also across hosts; the rules
        # found at the end apply to the origin we started from. self._resolving_robots (see __init__) is
        # set for this whole call, including every redirect hop's own _send() - never just the first one -
        # so none of them can recurse back into crawl_delay()/_rules() for any host.
        self._resolving_robots = True
        try:
            r = self._send(origin + "/robots.txt")
            for _ in range(5):
                if not (r.is_redirect and r.headers.get("location")):
                    break
                r = self._send(urljoin(str(r.url), r.headers["location"]))
        finally:
            self._resolving_robots = False
        if r.status_code == 200 and not _looks_like_robots_txt(r.headers.get("content-type", ""), r.text):
            # some custom-routed sites answer every unknown path, /robots.txt included, with their normal HTML page
            # (200). That is not a published robots policy: treat it as absent, i.e. allow (R1), not as its rules.
            log.warning("robots.txt for %s looks like an HTML page, not a robots policy; treating as absent", origin)
            return 404, ""
        # the body is also passed through on a 401/403/429 (normally an unconditional deny, RobotsCache's own
        # _DENY_STATUSES) so RobotsCache can inspect it for the cloud-storage-provider-error case (PM ruling,
        # 2026-09-22) - every other outcome for those statuses is completely unchanged, the body is just data.
        return r.status_code, (r.text if r.status_code == 200 or r.status_code in (401, 403, 429) else "")

    def _backoff(self, attempt: int, resp: Optional[httpx.Response]) -> float:
        delay = self._base * (2 ** attempt)
        if resp is not None and resp.headers.get("retry-after", "").isdigit():
            delay = max(delay, float(resp.headers["retry-after"]))
        return min(delay, self._cap)

    def _host_of(self, url: str) -> str:
        return urlsplit(url).netloc

    def _note_host_answered(self, host: str) -> None:
        """Any HTTP response at all - 200, 403, 429, 5xx - proves the host is reachable, so a run of
        transport failures on it is over and the counter starts again from zero."""
        if host in self._host_url_failures:
            del self._host_url_failures[host]

    def _note_host_url_exhausted(self, host: str, url: str, last_err: BaseException) -> None:
        """One more URL on this host exhausted its retries at the connection level. Only transport-level
        exhaustion reaches here; an HTTP-status give-up (a persistent 503, say) is a host that answered
        and is handled by the caller's own per-URL failure counting, never by this breaker."""
        n = self._host_url_failures.get(host, 0) + 1
        self._host_url_failures[host] = n
        if n >= MAX_CONSECUTIVE_HOST_FAILURES:
            log.error("%d consecutive URLs on %s failed at the connection level (last: %s); "
                      "treating the host as unreachable and not attempting its remaining URLs",
                      n, host, last_err)
            raise HostUnreachable(
                f"{n} consecutive URLs on {host} failed at the connection level (last: {last_err}); "
                f"the host is treated as unreachable, so its remaining URLs are not attempted",
                url=url) from last_err

    def _request_with_retries(self, url: str, max_bytes: Optional[int] = None,
                              extra_headers: Optional[Dict[str, str]] = None) -> httpx.Response:
        host = self._host_of(url)
        # F15: fail fast, BEFORE any network attempt or backoff sleep. This is what turns "grind 2,000
        # URLs for three hours" into "stop in seconds" - the cost of the remaining URLs is not paid.
        if self._host_url_failures.get(host, 0) >= MAX_CONSECUTIVE_HOST_FAILURES:
            raise HostUnreachable(
                f"{self._host_url_failures[host]} consecutive URLs on {host} already failed at the "
                f"connection level; not attempting {url}", url=url)
        last_err: Optional[BaseException] = None
        for attempt in range(self._max_retries + 1):
            resp: Optional[httpx.Response] = None
            try:
                resp = self._send(url, max_bytes, extra_headers)
                self._note_host_answered(host)
                if resp.status_code not in RETRY_STATUSES:
                    return resp
                last_err = FetchFailed(f"HTTP {resp.status_code} for {url}")
            except ResponseTooLarge:
                raise
            except httpx.TransportError as e:
                last_err = e
            if attempt < self._max_retries:
                d = self._backoff(attempt, resp)
                log.warning("retry %d/%d for %s in %.1fs (%s)", attempt + 1, self._max_retries, url, d, last_err)
                self._sleep(d)
        if isinstance(last_err, httpx.TransportError):
            # retries exhausted at the connection level - the only shape that feeds the breaker
            self._note_host_url_exhausted(host, url, last_err)
        raise FetchFailed(f"giving up on {url} after {self._max_retries + 1} attempts: {last_err}") from last_err

    # -- public -----------------------------------------------------------------------------
    def check(self, url: str, manifest: Optional[UrlManifest], *, redirect_hop: bool = False) -> None:
        if manifest is not None and not manifest.permits(url):
            log.error("manifest refuses %s", url)
            raise ManifestViolation(f"URL not in connector manifest: {url}", url=url)
        self._robots.require(url, self.user_agent, redirect_hop=redirect_hop)

    def allowed(self, url: str) -> bool:
        return self._robots.allowed(url, self.user_agent)

    def wait_turn(self, url: str) -> None:
        """Honour the per-host rate limit for a request made by something other than this client (the browser renderer)."""
        self._throttle(url)

    def robots_report(self, url: str) -> dict:
        return self._robots.report(url, self.user_agent)

    def get(self, url: str, *, manifest: Optional[UrlManifest] = None, max_bytes: Optional[int] = DEFAULT_MAX_BYTES,
           extra_headers: Optional[Dict[str, str]] = None) -> FetchResult:
        """``extra_headers`` is for a source's own optional credential (e.g. an API key some sources accept
        for a higher quota, never required) - it is never logged and never affects manifest/robots/rate-limit
        governance, which all run exactly as for any other request. ``max_bytes`` defaults to
        DEFAULT_MAX_BYTES (T23) - pass an explicit, larger value for a source known to need it (e.g. nsz.py's
        own PDF cap), or None only if a caller genuinely wants no cap at all (no caller in this project does)."""
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            # redirect_hop=True only from hop 1 on: the ORIGINAL host (hop 0) is always checked at full
            # strictness first, and only once it has explicitly allowed the route does a later hop's own
            # robots.txt become eligible for the narrower cloud-storage-error-as-absent interpretation
            # (PM ruling condition (c); see RobotsCache.require's docstring for the rest of the conditions).
            self.check(current, manifest, redirect_hop=hop > 0)
            resp = self._request_with_retries(current, max_bytes, extra_headers)
            if resp.is_redirect and resp.headers.get("location"):
                current = urljoin(current, resp.headers["location"])
                continue
            return FetchResult(url=url, final_url=current, status=resp.status_code, content=resp.content,
                               content_type=resp.headers.get("content-type"),
                               fetched_at=datetime.now(timezone.utc))
        raise FetchFailed(f"too many redirects starting at {url}")

    def post(self, url: str, json_body: dict, *, manifest: Optional[UrlManifest] = None,
             max_bytes: Optional[int] = DEFAULT_MAX_BYTES) -> FetchResult:
        """T18 ats_workday_cxs: Workday's CXS search endpoint requires a POST with a JSON body - the only
        connector in this project that needs one. Same governance as get() (manifest, robots, rate limit,
        certifi TLS, retry/backoff on 429/5xx/transport errors, a response size cap - T23); no redirect-
        following (none of this project's POST endpoints are known to redirect, and a redirected POST
        changes semantics enough that blindly re-sending the body would be its own bug - if one ever does,
        this raises FetchFailed rather than guessing what to do)."""
        self.check(url, manifest, redirect_hop=False)
        resp = self._post_with_retries(url, json_body, max_bytes)
        if resp.is_redirect:
            raise FetchFailed(f"POST {url} redirected (unsupported) to {resp.headers.get('location')}")
        return FetchResult(url=url, final_url=url, status=resp.status_code, content=resp.content,
                           content_type=resp.headers.get("content-type"), fetched_at=datetime.now(timezone.utc))

    def _post_with_retries(self, url: str, json_body: dict, max_bytes: Optional[int] = None) -> httpx.Response:
        last_err: Optional[BaseException] = None
        for attempt in range(self._max_retries + 1):
            resp: Optional[httpx.Response] = None
            try:
                self._throttle(url)
                headers = {"User-Agent": self.user_agent, "Accept-Language": "en", "Content-Type": "application/json"}
                resp = self._client.post(url, json=json_body, headers=headers, follow_redirects=False)
                self._last[urlsplit(url).netloc] = self._clock()
                # T23: a response size cap for POST too (Workday's CXS search API is expected to return small,
                # paginated JSON - a response far past that cap is never a legitimate page, only a real
                # response body length check after the fact, not full streaming like get()'s own cap, since
                # this project's only POST caller sends bounded page_size/limit itself).
                if max_bytes is not None and len(resp.content) > max_bytes:
                    raise ResponseTooLarge(f"POST {url}: body {len(resp.content)} bytes exceeds cap {max_bytes}")
                if resp.status_code not in RETRY_STATUSES:
                    return resp
                last_err = FetchFailed(f"HTTP {resp.status_code} for {url}")
            except httpx.TransportError as e:
                last_err = e
            if attempt < self._max_retries:
                d = self._backoff(attempt, resp)
                log.warning("retry %d/%d for POST %s in %.1fs (%s)", attempt + 1, self._max_retries, url, d, last_err)
                self._sleep(d)
        raise FetchFailed(f"giving up on POST {url} after {self._max_retries + 1} attempts: {last_err}") from last_err

    def close(self) -> None:
        self._client.close()


class Connector:
    """Base for every source connector. Subclasses set ``source_id`` and ``manifest``."""

    source_id: str = ""
    kind: str = ""  # a source.kind value; 'channel' sources are never fetched
    manifest: UrlManifest

    def __init__(self, client: PoliteClient, store: ObjectStore):
        if not self.source_id:
            raise TypeError("connector must define source_id")
        if self.kind == "channel":
            raise TypeError(f"{self.source_id} is a deeplink channel: it must not fetch anything")
        self.client, self.store = client, store

    def fetch_raw(self, url: str, kind: str = "html", max_bytes: Optional[int] = DEFAULT_MAX_BYTES) -> Tuple[FetchResult, RawRef]:
        res = self.client.get(url, manifest=self.manifest, max_bytes=max_bytes)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=res.content,
                      fetched_at=res.fetched_at, status=res.status, content_type=res.content_type,
                      final_url=res.final_url, robots_allowed=res.robots_allowed)
        return res, ref
