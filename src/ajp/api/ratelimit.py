"""Inbound rate limiting for the API's write endpoints (T49, packet-less, 2026-10-13).

WHY A LIMITER AND NOT A TOKEN: the endpoints that accept writes are, by product design, PUBLIC
self-service - a visitor uploads their CV to /v1/applicants or a thesis to /v1/thesis/analyze from the
web UI. A static bearer token cannot protect those without breaking the product (a browser cannot hold
a secret). What they actually lack is a bound on how OFTEN one client can do the expensive thing: each
upload is parsed, stored, and (for thesis similarity) can trigger real third-party API calls. That bound
is what a public deployment needs before anything else.

WHAT THIS IS: an in-process sliding-window counter, stdlib only - no new dependency, no Redis, no
shared state. Correct for the single-process deployment this project actually runs; a multi-worker
deployment would need a shared store, and that constraint is stated here rather than hidden.

WHAT IT IS NOT: not authentication. It bounds abuse volume; it does not identify anyone.

BUCKETS, and the reasoning for each default:
  upload  - POST /v1/applicants, POST /v1/thesis/analyze. The expensive ones: full document parse +
            store write. 60/hour per client is far beyond any real human use (a person uploads one CV)
            and far below what an abusive loop needs to matter.
  compute - POST .../matches, POST .../similarity. Recompute endpoints; similarity may call third-party
            services (real cost). More generous than upload because a legitimate user re-runs matches
            while tuning their profile, but still bounded.
  write   - PATCH/DELETE. Cheap, but they mutate or destroy a row keyed by an unguessable UUID, so they
            are bounded too - a rate limit on DELETE also caps the damage of a leaked capability URL.

KEY: the client's own address, or the first X-Forwarded-For hop when AJP_TRUST_PROXY_HEADERS=1. That
flag exists because BOTH choices are wrong somewhere: behind a reverse proxy every request shares the
proxy's address (one bucket for all visitors - the limiter would lock out the whole site), while
trusting that header when NOT behind a proxy lets a client mint unlimited buckets by sending a fake
header. Neither default is safe in both deployments, so the deployment declares which one it is.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Rule:
    max_requests: int
    window_seconds: int


# Defaults are deliberately generous against any real human and deliberately hostile to a loop.
DEFAULT_RULES: Dict[str, Rule] = {
    "upload": Rule(max_requests=60, window_seconds=3600),
    "compute": Rule(max_requests=240, window_seconds=3600),
    "write": Rule(max_requests=240, window_seconds=3600),
}

# Env override per bucket: AJP_RATE_LIMIT_<BUCKET>_PER_HOUR (a plain integer). Operators tune the
# numbers; the buckets themselves are code, so a mis-typed env name cannot silently disable a limit.
_ENV_VAR = "AJP_RATE_LIMIT_{bucket}_PER_HOUR"


def resolve_rules() -> Dict[str, Rule]:
    rules = dict(DEFAULT_RULES)
    for bucket, rule in rules.items():
        raw = os.environ.get(_ENV_VAR.format(bucket=bucket.upper()))
        if raw:
            try:
                n = int(raw)
            except ValueError:
                raise RuntimeError(f"{_ENV_VAR.format(bucket=bucket.upper())} must be an integer, got {raw!r}")
            if n <= 0:
                raise RuntimeError(f"{_ENV_VAR.format(bucket=bucket.upper())} must be positive, got {n}")
            rules[bucket] = Rule(max_requests=n, window_seconds=rule.window_seconds)
    return rules


def client_key(request: Request) -> str:
    """The client identity the limit is counted against. See the module docstring: which header to trust
    is a property of the DEPLOYMENT, not of this code, so it is declared via AJP_TRUST_PROXY_HEADERS."""
    if os.environ.get("AJP_TRUST_PROXY_HEADERS") == "1":
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return f"proxy:{first}"
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Sliding window per (bucket, client). ``hit`` returns the number of seconds until the oldest
    request in the window ages out (for Retry-After), or None when the request is allowed."""

    def __init__(self, rules: Dict[str, Rule]):
        self.rules = rules
        self._hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._last_prune = time.monotonic()

    def hit(self, bucket: str, client: str, *, now: float | None = None) -> None:
        """Count one request and raise 429 if the bucket's window is already full."""
        rule = self.rules.get(bucket)
        if rule is None:
            raise RuntimeError(f"unknown rate-limit bucket {bucket!r} (have {sorted(self.rules)})")
        now = time.monotonic() if now is None else now
        key = (bucket, client)
        window = self._hits[key]
        while window and now - window[0] >= rule.window_seconds:
            window.popleft()
        if len(window) >= rule.max_requests:
            retry_after = max(1, int(rule.window_seconds - (now - window[0])) + 1)
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded for {bucket}: max {rule.max_requests} per "
                       f"{rule.window_seconds // 60} minutes; retry after {retry_after}s",
                headers={"Retry-After": str(retry_after)},
            )
        window.append(now)
        # A client that stops hitting leaves its deque behind forever; prune the dead ones lazily so a
        # long-lived process does not grow one entry per client that ever connected.
        if now - self._last_prune > rule.window_seconds:
            self._prune(now, rule.window_seconds)
            self._last_prune = now

    def _prune(self, now: float, window_seconds: int) -> None:
        for key in list(self._hits):
            dq = self._hits[key]
            while dq and now - dq[0] >= window_seconds:
                dq.popleft()
            if not dq:
                del self._hits[key]

    def count(self, bucket: str, client: str) -> int:
        """Observability hook (used by tests and by any future /health style reporting)."""
        return len(self._hits.get((bucket, client), ()))


def rate_limit(bucket: str):
    """FastAPI dependency factory: ``Depends(rate_limit("upload"))`` on any endpoint that does the
    expensive thing. The limiter instance lives on app.state so a freshly built app starts with a clean
    slate - tests do not inherit one test's request history into the next."""

    def dep(request: Request) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        limiter.hit(bucket, client_key(request))

    return dep
