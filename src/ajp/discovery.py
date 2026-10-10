"""T20: two-stage bounded career-path discovery for an org given only its homepage - the addendum's own
ruling (2026-09-23 recon): (1) fetch the homepage HTML, harvest hrefs where the href OR its own anchor text
matches an EN+RS career/positions/vacancies/konkurs/posao keyword set; (2) only when that yields nothing,
fall back to a fixed path-list backfill. Capped at 12 FETCHED paths per org total (the homepage fetch itself
doesn't count against this budget - it is one fetch to find candidates, not a candidate itself).

This never activates anything: it only reports what it found (a candidate URL, with real HTTP evidence -
status/content-type/whether it looks like a real page) for a human/PM or a later task to act on. Whether a
found URL becomes career_urls on the org, or a registered ATS/board source, is a separate decision (state
machine: discovered -> dry_run -> active) - this module only ever produces 'discovered' entries.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import psycopg
from psycopg.rows import tuple_row
from selectolax.parser import HTMLParser

from ajp.connectors.base import (
    Connector, FetchFailed, FetchResult, ManifestViolation, ObjectStore, PoliteClient, UrlManifest,
)
from ajp.connectors.board import OrgManifest
from ajp.extract.text import decode_bytes
from ajp.robots import RobotsDenied

MAX_FETCHED_PATHS = 12

# EN + RS (Latin + Cyrillic) career-page keyword vocabulary, checked against BOTH the href's own path/query
# and the anchor's own visible text (a real site's nav link is routinely 'Careers' with an href like
# '/en/node/1234' - the href alone tells you nothing; the text is what actually says 'careers').
_EN_KEYWORDS = (
    "career", "careers", "job", "jobs", "vacanc", "employment", "recruitment", "work with us", "join us",
)
_RS_LATIN_KEYWORDS = (
    "konkurs", "posao", "poslovi", "zaposljavanje", "zaposljenje", "oglas", "izbor-u-zvanje", "izbori-u-zvanje",
)
_RS_CYRILLIC_KEYWORDS = (
    "конкурс", "посао", "послови", "запошљавање", "оглас",
)
_ALL_KEYWORDS = _EN_KEYWORDS + _RS_LATIN_KEYWORDS + _RS_CYRILLIC_KEYWORDS
_KEYWORD_RE = re.compile("|".join(re.escape(k) for k in _ALL_KEYWORDS), re.I)

# A generic backfill list only tried when the homepage's own nav gave no hit at all - Serbian and English
# path conventions actually observed across this project's earlier RS org fan-out (board.py/T8-T11) plus the
# plain English equivalents a non-RS org would use instead.
FIXED_PATHS = (
    "/konkursi", "/konkursi/", "/oglasi", "/oglasi/", "/zaposljavanje", "/posao", "/poslovi",
    "/izbori-u-zvanje", "/o-fakultetu/konkursi", "/career", "/careers", "/jobs", "/vacancies",
    "/en/careers", "/en/jobs", "/about/careers", "/working-with-us",
)


@dataclass
class DiscoveryResult:
    org_slug: str
    homepage: str
    # 'href_harvest' | 'fixed_path_backfill' | 'homepage_unreachable' | 'none' | 'soft_404_host'
    method: str
    found_urls: List[str] = field(default_factory=list)
    paths_fetched: int = 0              # against the MAX_FETCHED_PATHS budget (excludes the homepage/control probe)
    error: Optional[str] = None


def _keyword_candidates(html: str, base_url: str) -> List[str]:
    """Absolute URLs from every <a> whose own href OR visible text matches the keyword vocabulary,
    deduplicated, same-host only (an org's own homepage nav pointing at a genuinely different domain - a
    social-media icon, a partner site - is never a career-path candidate)."""
    tree = HTMLParser(html)
    base_host = urlsplit(base_url).netloc.lower()
    seen, out = set(), []
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        text = a.text() or ""
        if not (_KEYWORD_RE.search(href) or _KEYWORD_RE.search(text)):
            continue
        abs_url = urljoin(base_url, href)
        parts = urlsplit(abs_url)
        if parts.scheme not in ("http", "https") or parts.netloc.lower() != base_host:
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            out.append(abs_url)
    return out


class _Discoverer(Connector):
    """A throwaway Connector subclass purely to reuse PoliteClient's own governance (manifest/robots/rate-
    limit/retry) for the probing requests this module makes - never registered as a real source itself."""
    kind = "feed"

    def __init__(self, client: PoliteClient, store: ObjectStore, source_id: str, manifest: UrlManifest):
        self.source_id, self.manifest = source_id, manifest
        super().__init__(client, store)


MAX_REDIRECT_HOST_WIDENINGS = 3


def _fetch_homepage_resolving_redirects(d: _Discoverer, homepage: str) -> FetchResult:
    """Fetches the org's own homepage, WIDENING the connector's manifest one host at a time whenever a
    redirect is refused, instead of giving up - the same "read the org's own redirect, never guess" policy
    ats_workday.discover_workday_site() already uses for Workday tenant discovery, generalised: here the
    destination isn't a single known SaaS domain, so there is no pattern to pre-declare, only the org's own
    redirect chain to follow one confirmed hop at a time.

    Found live, 2026-09-23 (T20 PM correction): a real OpenAlex-listed org's own stated homepage genuinely
    redirects to a DIFFERENT registrable domain that IS still the same organisation (Sportska Akademija
    Beograd's sportsacademy.edu.rs -> www.spak.edu.rs; Institute for Recent History of Serbia's
    inisbgd.co.rs -> inis.bg.ac.rs) - the original, host-scoped-only manifest correctly refused to follow
    either blindly (never trust an unverified cross-domain redirect target), but that meant giving up
    entirely rather than reading back the REAL current host the org's own server just stated. Each widening
    below adds only the ONE host a real 3xx response from THIS org's own current URL just named - never a
    guess, never "any host" - capped at MAX_REDIRECT_HOST_WIDENINGS so a chain cannot grow unbounded."""
    known_hosts = {urlsplit(homepage).netloc}
    for _ in range(MAX_REDIRECT_HOST_WIDENINGS):
        try:
            return d.fetch_raw(homepage)[0]
        except ManifestViolation as e:
            if not e.url:
                raise
            new_host = urlsplit(e.url).netloc
            if not new_host or new_host in known_hosts:
                raise           # no new host to add (or the violation wasn't a host-widening case) - give up
            known_hosts.add(new_host)
            d.manifest = UrlManifest(*d.manifest.patterns, *OrgManifest.for_hosts([new_host]).patterns)
    return d.fetch_raw(homepage)[0]


def discover_career_paths(client: PoliteClient, store: ObjectStore, org_slug: str, homepage: str,
                          *, max_fetched_paths: int = MAX_FETCHED_PATHS) -> DiscoveryResult:
    """Stage 1: fetch the homepage, harvest keyword-matching hrefs, verify each with its own fetch (counts
    against the budget) - a candidate is only reported 'found' once a real 200 response confirms it, never
    from the href alone (D6: no unverified claim). Stage 2 (fixed-path backfill) only runs when stage 1
    yields not a single verified hit, and only for as many paths as the remaining budget allows."""
    try:
        manifest = OrgManifest.for_hosts([urlsplit(homepage).netloc])
    except ValueError as e:
        return DiscoveryResult(org_slug, homepage, "none", error=str(e))
    d = _Discoverer(client, store, f"discovery__{org_slug}", manifest)
    try:
        res = _fetch_homepage_resolving_redirects(d, homepage)
    except (FetchFailed, ManifestViolation, RobotsDenied) as e:
        return DiscoveryResult(org_slug, homepage, "homepage_unreachable", error=str(e))
    if res.status != 200:
        return DiscoveryResult(org_slug, homepage, "homepage_unreachable", error=f"HTTP {res.status}")

    # Soft-404 control probe (live-found, rgf.bg.ac.rs: EVERY path, including a nonsense one, returned
    # HTTP 200 with the byte-identical body - a real host behaviour, not hypothesised, that would otherwise
    # make every fixed_path_backfill entry a false positive). Not counted against the fetch budget - it is
    # calibration, not a candidate. A control fingerprint of None means the control itself failed to fetch
    # (transient/robots/etc) - candidates are then trusted at face value, same as before this fix existed.
    # base is the RESOLVED final URL (res.final_url), not the original homepage string - correct even for a
    # same-host redirect (http->https, a trailing-slash canonicalisation), and essential for the
    # cross-domain case above (subsequent fixed-path probing must target the org's REAL current host).
    base = res.final_url.rstrip("/")
    control_url = f"{base}/ajp-discovery-control-probe-{secrets.token_hex(8)}"
    control_fp: Optional[Tuple[int, int]] = None
    try:
        cres, _ = d.fetch_raw(control_url)
        if cres.status == 200:
            control_fp = (cres.status, len(cres.content))
    except (FetchFailed, ManifestViolation, RobotsDenied):
        pass

    def _is_real_hit(r) -> bool:
        if r.status != 200:
            return False
        return control_fp is None or (r.status, len(r.content)) != control_fp

    # base_url is res.final_url (the RESOLVED page), not the original homepage string - a relative href on
    # a page actually served by www.spak.edu.rs must resolve against THAT host, not the org's originally
    # stated (and possibly now-redirected-away-from) homepage.
    candidates = _keyword_candidates(decode_bytes(res.content, res.content_type), res.final_url)
    found: List[str] = []
    fetched = 0
    for url in candidates:
        if fetched >= max_fetched_paths:
            break
        fetched += 1
        try:
            r, _ = d.fetch_raw(url)
        except (FetchFailed, ManifestViolation, RobotsDenied):
            continue
        if _is_real_hit(r):
            found.append(url)
    if found:
        return DiscoveryResult(org_slug, homepage, "href_harvest", found, fetched)

    # Stage 2: fixed-path backfill, remaining budget only.
    for path in FIXED_PATHS:
        if fetched >= max_fetched_paths:
            break
        url = base + path
        fetched += 1
        try:
            r, _ = d.fetch_raw(url)
        except (FetchFailed, ManifestViolation, RobotsDenied):
            continue
        if _is_real_hit(r):
            found.append(url)
    if found:
        return DiscoveryResult(org_slug, homepage, "fixed_path_backfill", found, fetched)
    method = "soft_404_host" if control_fp is not None else "none"
    return DiscoveryResult(org_slug, homepage, method, found, fetched)


# --------------------------------------------------------------------- state machine (0017_discovery_state) ---
def record_discovery(conn: psycopg.Connection, result: DiscoveryResult) -> int:
    """Writes every found_url as a fresh 'discovered' row (idempotent - ON CONFLICT keeps the EXISTING
    state/promotion, never resets an already-promoted row back to 'discovered' on a re-run). Rows for an
    org whose discovery found nothing this run are simply not written to discovery_state - absence there
    means 'not yet discovered', not 'confirmed absent' (D6: never a false negative claim); record_run()
    below is what tells apart 'never probed' from 'probed, found nothing'."""
    n = 0
    for url in result.found_urls:
        conn.execute(
            """INSERT INTO discovery_state (org_slug, url, method)
               VALUES (%s, %s, %s)
               ON CONFLICT (org_slug, url) DO NOTHING""",
            (result.org_slug, url, result.method),
        )
        n += 1
    return n


def record_run(conn: psycopg.Connection, result: DiscoveryResult) -> None:
    """Logs EVERY discover_career_paths() call, hit or not - the governance dashboard's own 'requests per
    host'/'discovery status' data (0017_discovery_state's discovery_run_log)."""
    conn.execute(
        """INSERT INTO discovery_run_log (org_slug, homepage_host, method, paths_fetched, found_count, error)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (result.org_slug, urlsplit(result.homepage).netloc, result.method, result.paths_fetched,
         len(result.found_urls), result.error),
    )


def promote(conn: psycopg.Connection, org_slug: str, url: str, new_state: str, *, promoted_by: str,
           dry_run_rows: Optional[int] = None, ats_family: Optional[str] = None,
           ats_tenant: Optional[str] = None) -> bool:
    """Advances one discovered row to 'dry_run' or 'active'. Promotion to 'active' REQUIRES dry_run_rows to
    already be a non-null, non-negative count (the schema-valid-fixture-rows evidence this state machine's
    own rule requires) - never auto-promoted on a guess. Returns False (no-op) if the row doesn't exist or
    the active-without-evidence guard fires; never raises for either case, since a caller looping over many
    candidates must not have one bad row abort the whole batch."""
    if new_state == "active" and dry_run_rows is None:
        existing = conn.cursor(row_factory=tuple_row).execute(
            "SELECT dry_run_rows FROM discovery_state WHERE org_slug = %s AND url = %s", (org_slug, url)
        ).fetchone()
        if not existing or existing[0] is None:
            return False
    cur = conn.execute(
        """UPDATE discovery_state SET state = %s, promoted_by = %s, promoted_at = now(),
                  dry_run_rows = COALESCE(%s, dry_run_rows), ats_family = COALESCE(%s, ats_family),
                  ats_tenant = COALESCE(%s, ats_tenant)
           WHERE org_slug = %s AND url = %s""",
        (new_state, promoted_by, dry_run_rows, ats_family, ats_tenant, org_slug, url),
    )
    return cur.rowcount > 0


def governance_snapshot(conn: psycopg.Connection) -> Dict[str, Any]:
    """The T20 governance dashboard's own data, built entirely from what this module itself persisted
    (discovery_state's funnel + discovery_run_log's per-host activity) - scoped honestly to DISCOVERY's own
    requests, not a claim about every connector's lifetime request history across the whole platform (no
    such platform-wide counter exists yet; a real gap noted in this task's own done row, not papered over
    here with a number this module cannot actually back up).

    Uses its OWN tuple_row cursor regardless of the connection's default row_factory - this module is
    called both from the API layer (get_conn sets dict_row) and from plain scripts/tests (tuple by
    default), and must behave identically either way (the same fix T18's registry.py needed)."""
    cur = conn.cursor(row_factory=tuple_row)
    by_state = dict(cur.execute("SELECT state, count(*) FROM discovery_state GROUP BY state").fetchall())
    by_method = dict(cur.execute("SELECT method, count(*) FROM discovery_state GROUP BY method").fetchall())
    per_host = [
        {"host": h, "runs_24h": runs, "paths_fetched_24h": paths, "denied_24h": denied}
        for h, runs, paths, denied in cur.execute(
            """SELECT homepage_host, count(*), coalesce(sum(paths_fetched), 0),
                      count(*) FILTER (WHERE method = 'homepage_unreachable' AND error ILIKE '%%robot%%')
               FROM discovery_run_log WHERE run_at > now() - interval '24 hours'
               GROUP BY homepage_host ORDER BY homepage_host"""
        ).fetchall()
    ]
    total_denied = sum(h["denied_24h"] for h in per_host)
    return {
        "discovery_funnel": {"discovered": by_state.get("discovered", 0), "dry_run": by_state.get("dry_run", 0),
                             "active": by_state.get("active", 0)},
        "discovery_method": {"href_harvest": by_method.get("href_harvest", 0),
                             "fixed_path_backfill": by_method.get("fixed_path_backfill", 0)},
        "per_host_last_24h": per_host,
        "robots_denied_24h_total": total_denied,
    }


def coverage_report(conn: psycopg.Connection) -> Dict[str, Any]:
    """pm/outbox/coverage.json's own content - actual, queried counts only, never an extrapolated estimate
    presented as fact (the standing instruction this task was given). Same tuple_row-cursor convention as
    governance_snapshot above, for the same reason."""
    cur = conn.cursor(row_factory=tuple_row)
    total_orgs = cur.execute("SELECT count(*) FROM org_master WHERE country = 'RS'").fetchone()[0]
    with_career = cur.execute(
        "SELECT count(*) FROM org_master WHERE country = 'RS' AND array_length(career_urls, 1) > 0"
    ).fetchone()[0]
    runs = cur.execute(
        "SELECT method, count(*), sum(paths_fetched), sum(found_count) FROM discovery_run_log GROUP BY method"
    ).fetchall()
    by_method = {m: {"runs": n, "paths_fetched": pf or 0, "found": fc or 0} for m, n, pf, fc in runs}
    total_runs = sum(v["runs"] for v in by_method.values())
    gov = governance_snapshot(conn)
    return {
        "rs_orgs_total": total_orgs, "rs_orgs_with_career_urls": with_career,
        "discovery_runs_total": total_runs, "discovery_by_method": by_method,
        "discovery_funnel": gov["discovery_funnel"], "robots_denied_24h_total": gov["robots_denied_24h_total"],
    }
