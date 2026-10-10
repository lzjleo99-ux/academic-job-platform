"""academics.com as a deeplink-only external search channel (T13 wave A/B: PM asked for a judgment call - "if
its GCS-hosted gz sitemaps are robots-allowed and parseable in <=30 min, include; otherwise deeplink").

robots.txt (verified 2026-09-22) technically allows most paths and names a sitemap
(storage.googleapis.com/ac_prod_sitemap/prod/COM/sitemap.xml.gz), BUT the SAME file carries an explicit legal
notice: "The use of robots or other automated means to access academics.com or collect or mine data without
the express permission of academics.com is strictly prohibited" (citing Germany's UrhG SS44b text-and-data-
mining opt-out), with an email (info@academics.de) for requesting permission. This is a stated legal
restriction that overrides the merely-permissive technical robots.txt paths - the same discipline already
applied to EURAXESS (a written permitted-use request, see operator_actions.md) and never crawled without it.
This module never performs a request (must not import an HTTP client - enforced by
tests/test_governance_guards.py) and only generates a link the user's own browser opens.
"""
from __future__ import annotations

from typing import List, NamedTuple

import psycopg

SOURCE_ID = "academics_com"
BASE = "https://academics.com"
PERMISSION_CONTACT = "info@academics.de"


class Deeplink(NamedTuple):
    slug: str
    label: str
    url: str
    country_iso2: str
    description: str


def landing_link() -> str:
    return f"{BASE}/"


def deeplinks() -> List[Deeplink]:
    return [Deeplink("browse", "academics.com: academic jobs (opens on academics.com)", landing_link(), None,  # type: ignore[arg-type]
                     "A European academic job board. Its robots.txt states automated access is prohibited "
                     "without express permission - not crawled; see operator_actions.md for the pending "
                     "permission request.")]


def seed(conn: psycopg.Connection) -> None:
    """Idempotently create the channel source and its curated link."""
    conn.execute(
        """INSERT INTO source (id, kind, base_url, list_url, robots_ok, needs_browser, geo_scope, notes)
           VALUES (%s, 'channel', %s, %s, false, false, 'INTL',
                   'Deeplink-only: robots.txt states automated access is strictly prohibited without express '
                   'permission (UrhG SS44b opt-out) - a legal restriction, not just a technical one, honoured '
                   'even though the listed paths and sitemap are technically reachable. No server-side fetch '
                   'until written permission is obtained (operator action).')
           ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, notes = EXCLUDED.notes""",
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
