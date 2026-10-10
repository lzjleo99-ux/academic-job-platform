"""HigherEdJobs as a deeplink-only external search channel (T13 wave B).

Every RSS category feed (categoryFeed.cfm?catID=N) returns a 212-byte Imperva/Incapsula JavaScript challenge
page for a non-browser client (PM-verified 2026-09-22, this session's own robots check confirms the site is
otherwise robots-permissive - the block is an edge WAF, not a robots.txt policy). Passing that challenge would
be bot evasion, which is out of scope regardless of robots.txt; this module never performs a request (it must
not import an HTTP client - enforced by tests/test_governance_guards.py) and only generates links the user's
own browser opens on higheredjobs.com, plus seeds the source/channel_link rows for a curated set of the
academic category ids PM's own route recon already identified.
"""
from __future__ import annotations

from typing import List, NamedTuple

import psycopg

SOURCE_ID = "higheredjobs"
BASE = "https://www.higheredjobs.com"
# a representative slice of the ~43 academic-relevant category ids PM's recon found (t13_connector_routes.json)
# - not all 43 as separate rows (that's noise for a deeplink list a user browses by eye), one per broad area.
_CATEGORIES = {
    "faculty-general": (1, "Faculty Positions (all fields)"),
    "computer-science": (102, "Computer Science Faculty"),
    "data-science": (290, "Data Science Faculty"),
    "engineering": (110, "Engineering Faculty"),
    "sciences": (51, "Sciences Faculty"),
    "research-lab": (150, "Laboratory and Research Positions"),
    "medical-research": (137, "Medical Research"),
}


class Deeplink(NamedTuple):
    slug: str
    label: str
    url: str
    country_iso2: str
    description: str


def category_link(cat_id: int) -> str:
    return f"{BASE}/rss/categoryFeed.cfm?catID={cat_id}"


def category_browse_link(cat_id: int) -> str:
    # the human-browsable listing, not the (Imperva-walled) RSS feed itself - what a deeplink should send a
    # person's own browser to, since the feed URL alone would just show them the same JS challenge we get.
    return f"{BASE}/search/CategorySearch.cfm?CatID={cat_id}"


def landing_link() -> str:
    return f"{BASE}/"


def deeplinks() -> List[Deeplink]:
    links = [Deeplink("browse", "HigherEdJobs: browse all categories (opens on HigherEdJobs)", landing_link(),
                      None, "Landing page; every category and search filter is available there.")]  # type: ignore[arg-type]
    for slug, (cat_id, label) in _CATEGORIES.items():
        links.append(Deeplink(slug, f"HigherEdJobs: {label} (opens on HigherEdJobs)", category_browse_link(cat_id),
                              None, f"Category id {cat_id}, from PM's own route recon."))  # type: ignore[arg-type]
    return links


def seed(conn: psycopg.Connection) -> None:
    """Idempotently create the channel source and its curated links."""
    conn.execute(
        """INSERT INTO source (id, kind, base_url, list_url, robots_ok, needs_browser, geo_scope, notes)
           VALUES (%s, 'channel', %s, %s, false, false, 'INTL',
                   'Deeplink-only: robots_ok=false reflects that nothing is fetched here, not a robots.txt '
                   'policy - the site is robots-permissive, but every RSS category feed is behind an '
                   'Imperva/Incapsula JS challenge for a non-browser client (a 212-byte challenge page, not '
                   'real content); passing it would be bot evasion, out of scope regardless of robots.txt.')
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
