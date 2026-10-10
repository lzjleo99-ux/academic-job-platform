"""Galaxie/Odyssee (France, enseignants-chercheurs recruitment), T19 - deeplink-only, escalated to PM
(need_input row, 2026-09-23) rather than built, for two independent reasons:

1. galaxie.enseignementsup-recherche.gouv.fr's own robots.txt returns HTTP 403 (not absent/404 - a real
   deny, live-verified), which this project's own robots.py policy explicitly documents as
   "401, 403, 429, 5xx, network error -> deny everything (cached for fail_ttl)" - R1's own transient-failure
   convention, re-checked on later runs, never bypassed. Individual .htm content pages under /ensup/ DO
   return 200 for the same UA (so this is not a blanket bot block), but the robots.txt/bare-directory 403
   itself is what this project's own governance treats as a deny signal.

2. The addenda's own plan (bespoke two-index HTML tables + PDF detail via pdfplumber) targets a PLATFORM
   THAT NO LONGER PUBLISHES CURRENT LISTINGS: the ministry replaced Galaxie with a new platform, Odyssee
   (live since ~Oct 2024 for qualification, early 2025 for recruitment - "Odyssee remplace Galaxie",
   confirmed via a live web search + Galaxie's own page naming it:
   ensup/cand_recrutement_enseignants_chercheurs_Odyssee.htm), on a genuinely separate host,
   odyssee.enseignementsup-recherche.gouv.fr. That host's own /robots.txt returns 200 but its BODY is just
   the SPA's own index.html (an Angular app shell with a client-side catch-all route - live-verified: a
   nonsense path and a plausible /api/offres path both return the byte-identical 2214-byte shell), meaning
   there is no real ruleset to read AND no server-rendered content or discoverable API to build against
   without either guessing an unverified route (forbidden, D6/R7) or actually rendering the app (Playwright)
   to observe its real network calls - a legitimate, no-guessing way in, but a genuinely different build
   this packet did not scope, and one that would still need its own governance review (same bar Workday CXS
   got) once whatever access shape is found.

This module never performs a request (must not import an HTTP client - enforced by
tests/test_governance_guards.py) and only generates links the user's own browser opens.
"""
from __future__ import annotations

from typing import List, NamedTuple

import psycopg

SOURCE_ID = "galaxie_fr"
GALAXIE_BASE = "https://www.galaxie.enseignementsup-recherche.gouv.fr"
ODYSSEE_BASE = "https://odyssee.enseignementsup-recherche.gouv.fr"


class Deeplink(NamedTuple):
    slug: str
    label: str
    url: str
    country_iso2: str
    description: str


def deeplinks() -> List[Deeplink]:
    return [
        Deeplink("galaxie", "Galaxie: legacy enseignants-chercheurs recruitment listings (opens on galaxie.gouv.fr)",
                f"{GALAXIE_BASE}/ensup/cand_recrutement_enseignants_chercheurs.htm", "FR",
                "France's legacy academic recruitment portal - robots.txt returns HTTP 403 (a real deny, not "
                "absent), transient per R1; not crawled while that stands."),
        Deeplink("odyssee", "Odyssee: current enseignants-chercheurs recruitment platform (opens on odyssee.gouv.fr)",
                f"{ODYSSEE_BASE}/", "FR",
                "Replaced Galaxie for recruitment since early 2025. A JS single-page app with a client-side "
                "catch-all route and no discoverable server-rendered listing or public API found by "
                "verification alone - would need a scoped Playwright-based investigation, not built yet."),
    ]


def seed(conn: psycopg.Connection) -> None:
    """Idempotently create the channel source and its curated links."""
    conn.execute(
        """INSERT INTO source (id, kind, base_url, list_url, robots_ok, needs_browser, geo_scope, notes)
           VALUES (%s, 'channel', %s, %s, false, false, 'FR',
                   'Deeplink-only: Galaxie''s own robots.txt returns HTTP 403 (deny per this project''s own '
                   'robots.py policy, transient per R1) and the platform is itself superseded by Odyssee, a '
                   'JS SPA with no discoverable public listing route without a scoped Playwright '
                   'investigation (escalated to PM, need_input, 2026-09-23).')
           ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, notes = EXCLUDED.notes""",
        (SOURCE_ID, GALAXIE_BASE, f"{GALAXIE_BASE}/ensup/cand_recrutement_enseignants_chercheurs.htm"),
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
