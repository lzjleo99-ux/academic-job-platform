"""Jobbnorge (jobbnorge.no, the Norwegian academic/public-sector job portal) as a deeplink-only external
search channel (T13 wave B). Its API (api.jobbnorge.no/assignment) answers 401 without a key (PM-confirmed,
this session's own check agrees) - obtaining one is an operator action (see pm/outbox/operator_actions.md),
not something this module can do. This module never performs a request (must not import an HTTP client -
enforced by tests/test_governance_guards.py) and only generates a link the user's own browser opens.
"""
from __future__ import annotations

from typing import List, NamedTuple

import psycopg

SOURCE_ID = "jobbnorge"
BASE = "https://www.jobbnorge.no"


class Deeplink(NamedTuple):
    slug: str
    label: str
    url: str
    country_iso2: str
    description: str


def search_link() -> str:
    return f"{BASE}/en/jobs"


def deeplinks() -> List[Deeplink]:
    return [Deeplink("browse", "Jobbnorge: academic and public-sector jobs (opens on Jobbnorge)", search_link(),
                     "NO", "Norway's academic/public-sector job portal. Its API needs a key we do not have yet "
                          "(see operator_actions.md); this is a landing page, not a pre-filtered search.")]


def seed(conn: psycopg.Connection) -> None:
    """Idempotently create the channel source and its curated link."""
    conn.execute(
        """INSERT INTO source (id, kind, base_url, list_url, robots_ok, needs_browser, geo_scope, notes)
           VALUES (%s, 'channel', %s, %s, false, false, 'NO',
                   'Deeplink-only: the API requires a key we do not have (401, operator action to obtain one); '
                   'no server-side fetch until then.')
           ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, notes = EXCLUDED.notes""",
        (SOURCE_ID, BASE, search_link()),
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
