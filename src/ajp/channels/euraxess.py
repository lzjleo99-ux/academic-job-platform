"""EURAXESS as a deeplink-only external search channel (packet 003, part B).

euraxess.ec.europa.eu/robots.txt disallows fetching job search and job pages, so this module
never performs a request (it must not import an HTTP client). It only *generates* links that the
end user's browser opens on EURAXESS, and seeds the ``source`` / ``channel_link`` rows for them.
This file is the only place in the code base allowed to contain EURAXESS job-search URLs
(enforced by tests/test_governance_guards.py) - including euraxess.rs below, a genuinely SEPARATE
host (the Serbian national EURAXESS portal, not the pan-European one), kept in this same module
so that "one canonical place for EURAXESS URLs" stays literally true rather than needing the guard
widened to a second module.

Facet ids are PM-supplied (packet 002) and curated by hand; the 483-country map is deliberately
not harvested: the user picks any other country inside EURAXESS.

euraxess.rs: T19 - deeplink-only, escalated to PM (need_input row, 2026-09-23), NOT the real HTML
connector the addenda's own R_euraxess_html_connector ruling called for. LIVE-VERIFIED 2026-09-23:
euraxess.rs's own robots.txt (served at www.euraxess.rs after a 301) is a normal Drupal 10 file that
explicitly `Allow: /jobs` - technically permissive. But the actual /jobs path (and the addenda's own
named route, /jobs/search/country/serbia-1097) returns HTTP 403 "Access denied", and the 403 page's
own markup names the real cause: a Drupal "Antibot" module CSS rule
(`form.antibot > * { display: none !important; }`) - a genuine bot-challenge mechanism, the same
category as HERC's own bot-challenge wall the addenda already ruled must never be bypassed. The
site's HOMEPAGE itself returns 200 (not a blanket UA block), only /jobs is challenge-walled.
"""
from __future__ import annotations

from typing import List, NamedTuple

import psycopg

SOURCE_ID = "euraxess"
BASE = "https://euraxess.ec.europa.eu"
SERBIA_COUNTRY_FACET_ID = 786

RS_SOURCE_ID = "euraxess_rs"
RS_BASE = "https://euraxess.rs"


class Deeplink(NamedTuple):
    slug: str
    label: str
    url: str
    country_iso2: str
    description: str


def serbia_search_link() -> str:
    return f"{BASE}/jobs/search?f%5B0%5D=job_country%3A{SERBIA_COUNTRY_FACET_ID}"


def landing_link() -> str:
    return f"{BASE}/jobs"


def deeplinks() -> List[Deeplink]:
    return [
        Deeplink("serbia-jobs", "Serbia: research jobs on EURAXESS (opens on EURAXESS)", serbia_search_link(), "RS",
                 "EURAXESS job search pre-filtered to Serbia. Opens in your browser on euraxess.ec.europa.eu; "
                 "these results are not part of our database."),
        Deeplink("jobs-landing", "EURAXESS jobs: choose any country (opens on EURAXESS)", landing_link(), None,  # type: ignore[arg-type]
                 "EURAXESS jobs landing page; pick country and filters there."),
    ]


def rs_deeplinks() -> List[Deeplink]:
    return [
        Deeplink("serbia-jobs", "EURAXESS Serbia: research jobs (opens on euraxess.rs)",
                f"{RS_BASE}/jobs/search/country/serbia-1097", "RS",
                "The Serbian national EURAXESS portal's own job search - /jobs is Drupal Antibot "
                "bot-challenge-walled (HTTP 403 'Access denied', live-verified), never bypassed."),
    ]


def seed(conn: psycopg.Connection) -> None:
    """Idempotently create the channel source and its curated links."""
    conn.execute(
        """INSERT INTO source (id, kind, base_url, list_url, robots_ok, needs_browser, geo_scope, notes)
           VALUES (%s, 'channel', %s, %s, false, false, 'europe',
                   'Deeplink-only: robots.txt disallows /jobs/search and /jobs/*; no server-side fetch. '
                   'Data access requires a partnership/feed (see operator_actions.md).')
           ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, robots_ok = false, notes = EXCLUDED.notes""",
        (SOURCE_ID, BASE, landing_link()),
    )
    for d in deeplinks():
        conn.execute(
            """INSERT INTO channel_link (source_id, slug, label, url, country_iso2, description)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (source_id, slug) DO UPDATE
                 SET label = EXCLUDED.label, url = EXCLUDED.url,
                     country_iso2 = EXCLUDED.country_iso2, description = EXCLUDED.description""",
            (SOURCE_ID, d.slug, d.label, d.url, d.country_iso2, d.description),
        )
    conn.execute(
        """INSERT INTO source (id, kind, base_url, list_url, robots_ok, needs_browser, geo_scope, notes)
           VALUES (%s, 'channel', %s, %s, false, false, 'RS',
                   'Deeplink-only: /jobs is Drupal Antibot bot-challenge-walled (HTTP 403 Access denied, '
                   'live-verified 2026-09-23) - never bypassed, same discipline as HERC. Contradicts the '
                   'addenda''s own R_euraxess_html_connector ruling, which assumed this route was openly '
                   'crawlable; escalated to PM (need_input) rather than built.')
           ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, notes = EXCLUDED.notes""",
        (RS_SOURCE_ID, RS_BASE, f"{RS_BASE}/jobs/search/country/serbia-1097"),
    )
    for d in rs_deeplinks():
        conn.execute(
            """INSERT INTO channel_link (source_id, slug, label, url, country_iso2, description)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (source_id, slug) DO UPDATE
                 SET label = EXCLUDED.label, url = EXCLUDED.url,
                     country_iso2 = EXCLUDED.country_iso2, description = EXCLUDED.description""",
            (RS_SOURCE_ID, d.slug, d.label, d.url, d.country_iso2, d.description),
        )
