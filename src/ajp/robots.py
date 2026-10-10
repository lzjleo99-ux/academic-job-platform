"""robots.txt cache (D5: obey robots.txt). RFC 9309 matching via Protego (wildcards, longest match).

Failure policy (deliberately conservative, stricter than RFC 9309 for 401/403/429):
  * 200                 -> rules cached for ``ttl`` (24h)
  * 404, 410, other 4xx -> "no robots.txt": everything allowed
  * 401, 403, 429, 5xx, network error -> deny everything (cached for ``fail_ttl``);
    if a previous copy exists it keeps governing for up to 2*ttl after it was fetched,
    after which persistent errors mean deny.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

import httpx
from protego import Protego

log = logging.getLogger("ajp.robots")

Fetcher = Callable[[str], Tuple[int, str]]  # origin -> (status, body); may raise httpx.HTTPError
_DENY_STATUSES = {401, 403, 429}

# PM ruling 2026-09-22 (jobs.ac.uk's sitemapindex.xml redirects to a SHARED, multi-tenant S3 endpoint whose own
# bucket-root robots.txt returns AWS's generic AccessDenied XML - not a per-tenant policy for anyone's content,
# just an artifact of shared cloud-storage hosting). A robots.txt response that is a known cloud-storage
# provider's ERROR document, with ZERO robots directives in it, is treated as ABSENT rather than a deny - but
# ONLY when interpreting robots for a REDIRECT HOP reached while resolving a request whose ORIGINAL host already
# explicitly allows the route (see PoliteClient.get's redirect loop); a 403 on the actual target object/resource
# itself, or on a redirect hop's robots.txt with a real (even if restrictive) directive body, stays deny/
# transient exactly as before. Covers S3 (<Error><Code>AccessDenied|NoSuchKey|NoSuchBucket</Code>...), GCS and
# Azure Blob Storage, which all use the same <Error><Code>...</Code> XML error shape.
_CLOUD_STORAGE_ERROR_CODE = re.compile(
    r"<Error>\s*<Code>(AccessDenied|NoSuchKey|NoSuchBucket|[A-Za-z]*NotFound|[A-Za-z]*Forbidden)</Code>", re.I)
_ROBOTS_DIRECTIVE = re.compile(r"^\s*(user-agent|allow|disallow|crawl-delay|sitemap)\s*:", re.I | re.M)


def _looks_like_cloud_storage_error(body: str) -> bool:
    return bool(_CLOUD_STORAGE_ERROR_CODE.search(body)) and not _ROBOTS_DIRECTIVE.search(body)


class RobotsDenied(PermissionError):
    """reason: 'disallowed' (robots.txt forbids it: permanent) or 'unavailable' (robots.txt unreachable: transient, R1)."""
    def __init__(self, message: str, reason: str = "disallowed"):
        super().__init__(message)
        self.reason = reason


@dataclass
class _Entry:
    fetched_at: float          # when the rules were obtained
    rules: Optional[Protego]   # None = deny everything
    ok: bool                   # rules come from a successful fetch (or an authoritative 4xx)
    retry_at: float = 0.0      # do not refetch before this time after a failure
    status: int = 0            # HTTP status of the last robots.txt attempt (599 = network/TLS error)


class RobotsCache:
    def __init__(self, fetcher: Fetcher, *, ttl: float = 86400.0, fail_ttl: float = 900.0,
                 clock: Callable[[], float] = time.monotonic):
        self._fetch, self._ttl, self._fail_ttl, self._clock = fetcher, ttl, fail_ttl, clock
        self._cache: Dict[str, _Entry] = {}

    def _usable(self, e: _Entry, now: float) -> Optional[Protego]:
        return e.rules if e.ok and now - e.fetched_at < 2 * self._ttl else None

    def _rules(self, origin: str, *, redirect_hop: bool = False) -> Optional[Protego]:
        now = self._clock()
        e = self._cache.get(origin)
        if e:
            if e.ok and now - e.fetched_at < self._ttl:
                return e.rules
            if now < e.retry_at:
                return self._usable(e, now)
        try:
            status, body = self._fetch(origin)
        except httpx.HTTPError as err:
            status, body = 599, ""
            log.warning("robots.txt unreachable for %s: %s", origin, type(err).__name__)
        if status == 200 or (400 <= status < 500 and status not in _DENY_STATUSES):
            rules = Protego.parse(body if status == 200 else "")
            self._cache[origin] = _Entry(now, rules, True, status=status)
            return rules
        if redirect_hop and status in _DENY_STATUSES and _looks_like_cloud_storage_error(body):
            # PM ruling: a cloud-storage provider's own error document (not a per-tenant policy) reached only
            # via a redirect hop - treated as absent (allow). Cached the SAME way the HTML-catch-all-robots.txt
            # case above already is (ok=True): a later same-origin lookup (e.g. crawl_delay(), which has no
            # redirect_hop context of its own) must see the SAME resolved outcome, not independently re-fetch
            # and re-interpret the identical 403 without that context and cache a conflicting deny (this
            # happened live - jobs.ac.uk's own Crawl-delay check on the just-redirected sitemap0.xml request
            # re-triggered a robots.txt fetch for the S3 origin and, lacking redirect_hop, cached a deny that
            # then poisoned the next require() call for the same origin).
            log.warning("robots.txt for %s is a cloud-storage provider error, not a policy; treating as absent (redirect hop)", origin)
            rules = Protego.parse("")
            self._cache[origin] = _Entry(now, rules, True, status=status)
            return rules
        log.warning("robots.txt for %s returned %s", origin, status)
        if e and e.ok and now - e.fetched_at < 2 * self._ttl:   # a recent good copy keeps governing
            e.retry_at = now + self._fail_ttl
            e.status = status
            return e.rules
        self._cache[origin] = _Entry(now, None, False, now + self._fail_ttl, status)
        return None

    def report(self, url: str, user_agent: str) -> dict:
        """Why a URL is (not) fetchable: robots status label, HTTP status and the verdict."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        allowed = self.allowed(url, user_agent)
        e = self._cache.get(origin)
        status = e.status if e else 0
        label = ("published" if status == 200 else "unpublished" if e and e.ok else "unavailable")
        return {"origin": origin, "robots": label, "http_status": status, "allowed": allowed}

    def allowed(self, url: str, user_agent: str, *, redirect_hop: bool = False) -> bool:
        parts = urlsplit(url)
        rules = self._rules(f"{parts.scheme}://{parts.netloc}", redirect_hop=redirect_hop)
        return rules is not None and rules.can_fetch(url, user_agent)

    def crawl_delay(self, url: str, user_agent: str) -> Optional[float]:
        """The site's own declared Crawl-delay (seconds) for the matching agent group, or None if it states
        none / robots.txt is absent or unavailable. Reuses the same cached rules _rules() already fetched -
        no extra request. The caller (PoliteClient) is responsible for applying a floor/cap; this returns the
        raw declared value only."""
        parts = urlsplit(url)
        rules = self._rules(f"{parts.scheme}://{parts.netloc}")
        return rules.crawl_delay(user_agent) if rules is not None else None

    def require(self, url: str, user_agent: str, *, redirect_hop: bool = False) -> None:
        """redirect_hop=True only for a URL reached by following a redirect FROM an origin whose own robots.txt
        already explicitly allowed the original route (PoliteClient.get's redirect loop, condition (c) of the
        cloud-storage-error ruling) - it relaxes ONLY the cloud-storage-error-as-absent interpretation above,
        never any other robots outcome (a real Disallow on the hop's own host still denies exactly as before)."""
        parts = urlsplit(url)
        rules = self._rules(f"{parts.scheme}://{parts.netloc}", redirect_hop=redirect_hop)
        if rules is None:
            log.error("robots.txt unavailable for %s", url)
            raise RobotsDenied(f"robots.txt unavailable for {url}", reason="unavailable")
        if not rules.can_fetch(url, user_agent):
            log.error("robots.txt disallows %s", url)
            raise RobotsDenied(f"robots.txt disallows {url}", reason="disallowed")
