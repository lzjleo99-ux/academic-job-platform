"""Infostud (poslovi.infostud.com, a general Serbian jobs board) as a deeplink-only external search channel
(T13 wave B). PM's condition: build a real connector only if can_fetch(/oglasi-za-posao?page=2)=true AND an
allowed academic filter exists. This session verified the FIRST half live (both page 1 and page 2 of
/oglasi-za-posao are robots-allowed), but found no academic/education/science filter mechanism on the listing
page (checked category links and path segments; only unrelated 'za-medije' press categories exist) - the
conjunctive condition is not met, so this stays deeplink-only rather than building a connector that would have
to scrape all ~120 pages of every industry and classify after the fact (the same overlap with NSZ/sljaka PM's
own note already flagged, at a much higher cost). This module never performs a request (must not import an
HTTP client - enforced by tests/test_governance_guards.py) and only generates a link the user's own browser opens.
"""
from __future__ import annotations

from typing import List, NamedTuple

import psycopg

SOURCE_ID = "infostud"
BASE = "https://poslovi.infostud.com"


class Deeplink(NamedTuple):
    slug: str
    label: str
    url: str
    country_iso2: str
    description: str


def listing_link() -> str:
    return f"{BASE}/oglasi-za-posao"


def deeplinks() -> List[Deeplink]:
    return [Deeplink("browse", "Poslovi Infostud: svi oglasi (opens on Infostud)", listing_link(), "RS",
                     "A general (all-industry) Serbian jobs board; no academic-specific filter was found "
                     "(checked live) to justify a dedicated connector - see PM's own conditional build rule.")]


def seed(conn: psycopg.Connection) -> None:
    """Idempotently create the channel source and its curated link."""
    conn.execute(
        """INSERT INTO source (id, kind, base_url, list_url, robots_ok, needs_browser, geo_scope, notes)
           VALUES (%s, 'channel', %s, %s, true, false, 'RS',
                   'Deeplink-only per PM''s own conditional rule: robots ALLOWS /oglasi-za-posao (both page 1 '
                   'and page 2, verified live), but no academic/education/science filter mechanism exists on '
                   'the listing to narrow it without scraping every industry - not built as a connector.')
           ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, notes = EXCLUDED.notes""",
        (SOURCE_ID, BASE, listing_link()),
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
