"""The single source of truth for "seed everything a fresh install needs": every connector's own
ensure_source(), every deeplink channel's own seed(), the base RS org seed AND (T20/T21) the OpenAlex
reconciliation supplement, plus a couple of synthetic FX rates so salary EUR/year conversion has something
to divide by offline. Both tools/dev_up.py (the existing local dev-stack script) and ajp.cli.init (T21's
packaged `ajp-init` console script, run from a pip install rather than a repo checkout) call this - one
definition, so a new seed never has to be added in two places to actually take effect everywhere.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import psycopg

from ajp import orgs
from ajp.channels import academics_com, euraxess, galaxie_fr, higheredjobs, infostud, jobbnorge
from ajp.connectors.academicpositions import AcademicPositionsConnector
from ajp.connectors.academictransfer import AcademicTransferConnector
from ajp.connectors.ats_workday import WorkdayCxsConnector, WorkdaySite
from ajp.connectors.base import PoliteClient
from ajp.connectors.board import BoardConnector
from ajp.connectors.cern import CernConnector
from ajp.connectors.cnrs import CnrsConnector
from ajp.connectors.gen_sitemap_html import SitemapBoardConfig, SitemapHtmlConnector
from ajp.connectors.gen_wordpress import WordPressBoardConfig, WordPressConnector
from ajp.connectors.higher_ed_jobsrss import CHRONICLE_JOBS, INSIDE_HIGHER_ED, HigherEdJobsRssConnector
from ajp.connectors.inria import InriaConnector
from ajp.connectors.jobs_ac_uk import JobsAcUkConnector
from ajp.connectors.kth import KthConnector
from ajp.connectors.ku_dk import KuDkConnector
from ajp.connectors.nature_careers import NatureCareersConnector
from ajp.connectors.nsz import NszConnector
from ajp.connectors.sljaka import SljakaConnector
from ajp.ingest import upsert_listing_deduped
from ajp.rawstore import LocalFsStore, put_raw

_SUPPLEMENT_PATH = Path(__file__).parent / "seeds" / "rs_orgs_openalex_supplement.json"
_DEMO_FIXTURE_PATH = Path(__file__).parent / "seeds" / "dev_listings_fixture.json"


def load_supplement_seed() -> list:
    """The T20/T21 OpenAlex-reconciliation supplement (86 orgs at last reconciliation) - packaged inside
    src/ajp/seeds/ (not tools/), so it ships with `pip install .` like the base seed does."""
    if not _SUPPLEMENT_PATH.exists():
        return []
    return json.loads(_SUPPLEMENT_PATH.read_text(encoding="utf-8"))["orgs"]


def ensure_sources(conn: psycopg.Connection, store: LocalFsStore) -> None:
    """Every connector's own ensure_source() plus every deeplink channel's own seed() - the org-independent
    half of a fresh install's data (org_master rows come from seed_all_orgs() below instead)."""
    client = PoliteClient(rps=1)          # never used to fetch here - ensure_source() only writes to `source`
    BoardConnector.ensure_source(conn)
    SljakaConnector.ensure_source(conn)
    AcademicTransferConnector.ensure_source(conn)
    KuDkConnector.ensure_source(conn)
    KthConnector.ensure_source(conn)
    InriaConnector.ensure_source(conn)
    # T13's own tier-1 aggregators - PM found live (2026-09-24, T21 acceptance audit) that these 5 real,
    # already-built, already-tested connectors were never registered by ANY seeding path at all (this
    # function is the ONLY place that calls ensure_source() for a fresh install), so a real deployment would
    # silently never schedule/run any of them despite the code fully existing and working.
    CernConnector.ensure_source(conn)
    CnrsConnector.ensure_source(conn)
    AcademicPositionsConnector.ensure_source(conn)
    JobsAcUkConnector.ensure_source(conn)
    NatureCareersConnector.ensure_source(conn)
    # EMBL's tenant/site discovered live (2026-09-22/23) via its own careers-page redirect - never guessed,
    # never hardcoded from a route someone else published (T18 rule); re-verified live again 2026-09-23.
    WorkdayCxsConnector.ensure_source(conn, WorkdaySite(tenant="embl", wd_host="wd103", site="EMBL"))
    # Science Fund of Serbia (fondzanauku.gov.rs): live-verified 2026-09-23. The real category feed path is
    # the Cyrillic '/category/позиви/feed/' slug (percent-encoded) - the page's own <link rel="alternate">
    # autodiscovery tag is what surfaced it; an English '/category/calls/feed/' guess 301s to the category's
    # HTML page, not its feed, and would have silently returned zero items forever. default_kind='grant_call'
    # because this category is entirely funding calls (PM's own T18 review finding).
    fund_config = WordPressBoardConfig(
        source_id="gen_wordpress__fondzanauku", host="fondzanauku.gov.rs", org_raw="Fond za nauku (Science Fund of Serbia)",
        classifier="rs", fixed_country="RS", default_kind="grant_call",
        category_feed_paths=("/category/%D0%BF%D0%BE%D0%B7%D0%B8%D0%B2%D0%B8/feed/",),
    )
    WordPressConnector.ensure_source(conn, fund_config, f"https://{fund_config.host}{fund_config.category_feed_paths[0]}")
    # University of Kragujevac, Faculty of Education in Jagodina (pefja.kg.ac.rs): live-verified 2026-09-23.
    # A general news/events blog feed (student camps, festivals, ...), not a dedicated vacancy board - real
    # academic calls appear rarely among it (1/10 most-recent items, live) - rs_text's own title gate is
    # relied on to filter the rest, same "general board, strict gate, high discard rate expected" shape as
    # sljaka.com, closing the "Kragujevac career URL undiscovered since packet 001" gap PM's own addenda flagged.
    pefja_config = WordPressBoardConfig(
        source_id="gen_wordpress__pefja", host="pefja.kg.ac.rs",
        org_raw="Fakultet pedagoških nauka, Univerzitet u Kragujevcu (Jagodina)", classifier="rs", fixed_country="RS",
    )
    WordPressConnector.ensure_source(conn, pefja_config, "https://pefja.kg.ac.rs/feed/")
    # THEunijobs (Times Higher Education): live-verified 2026-09-23, replaces the dead unijobs.com.au route
    # PM's addenda flagged (NXDOMAIN). jsonld mode: each detail page carries its own schema.org JobPosting,
    # own country per row - a real multi-employer aggregator, not a single org's board (grouping.py's
    # _AGGREGATOR_ATS_FAMILIES already reflects this).
    theunijobs_config = SitemapBoardConfig(
        source_id="gen_sitemap_html__theunijobs", host="www.timeshighereducation.com",
        sitemapindex_url="https://www.timeshighereducation.com/unijobs/sitemapindex.xml",
        shard_url_pattern=r"/unijobs/sitemap2-\d+\.xml$", detail_url_pattern=r"/unijobs/listing/\d+/",
        detail_mode="jsonld",
    )
    SitemapHtmlConnector.ensure_source(conn, theunijobs_config)
    NszConnector(client, store).ensure_source(conn)
    # T27 (packet 015): InsideHigherEd + The Chronicle of Higher Education Jobs - both live-verified
    # 2026-09-28 as real, robots-permissive RSS feeds (same underlying platform, one config-driven
    # connector). HigherEdJobs (a third site named in the same packet) stays deeplink-only - re-verified
    # live 2026-09-28 as still Incapsula-challenge-walled, the SAME finding T13 wave B already made
    # 2026-09-22 (see channels/higheredjobs.py's own docstring); never bypassed (R1/R5).
    HigherEdJobsRssConnector.ensure_source(conn, INSIDE_HIGHER_ED)
    HigherEdJobsRssConnector.ensure_source(conn, CHRONICLE_JOBS)
    for mod in (euraxess, academics_com, higheredjobs, jobbnorge, infostud, galaxie_fr):
        mod.seed(conn)
    # T25 (packet 016 PM ruling on razvojkarijere, 2026-09-28): the University of Belgrade Career Development
    # Centre is now a real org_master row (slug=bg-razvojkarijere, type=career_centre) instead of the earlier,
    # ad-hoc standalone channel this used to be (channels/bg_career_centre.py, retired - seed_all_orgs()'s own
    # deeplink-only auto-channel now covers it, one canonical place instead of two parallel representations of
    # the same organisation).


def seed_all_orgs(conn: psycopg.Connection) -> Tuple[int, int]:
    """Base RS seed + the OpenAlex reconciliation supplement (R3: 'clean bootstrap seeds all 186 orgs with
    asserted counts') - one call, so a fresh install never silently ends up with only the base 100 the way
    tools/dev_up.py's own pre-T21 version did (the PM-found T20 acceptance gap this fixes at the root, not
    just in that one script)."""
    base = orgs.load_seed()
    orgs.seed_orgs(conn, base, country="RS")
    supplement = load_supplement_seed()
    if supplement:
        orgs.seed_orgs(conn, supplement, country="RS")
    return len(base), len(supplement)


def seed_fx(conn: psycopg.Connection) -> None:
    """A couple of synthetic FX rates so the salary EUR/year conversion has something to divide by offline -
    clearly marked source='DEV_FIXTURE', never mistaken for an ECB/NBS/ERAPI snapshot (fx_source_rank ranks an
    unrecognised source last, so a real `python -m ajp.fx` run always outranks this once one has been done)."""
    for currency, per_eur in (("GBP", 0.86), ("USD", 1.09), ("RSD", 117.2)):
        conn.execute(
            """INSERT INTO fx_rate (rate_date, currency, per_eur, source) VALUES (CURRENT_DATE, %s, %s, 'DEV_FIXTURE')
               ON CONFLICT (rate_date, currency, source) DO UPDATE SET per_eur = EXCLUDED.per_eur""",
            (currency, per_eur),
        )


def assert_org_count(conn: psycopg.Connection, expected: Optional[int] = None) -> int:
    """R3's own 'assert counts at init and fail loudly on drift' - raises, never just logs a warning, since
    a silently-short seed is exactly the bug PM's own T20 acceptance caught once already. `expected`
    defaults to len(base seed) + len(supplement) computed fresh from the seed files themselves, never a
    hardcoded number that would silently go stale the next time either seed file's own row count changes."""
    if expected is None:
        expected = len(orgs.load_seed()) + len(load_supplement_seed())
    actual = conn.execute("SELECT count(*) FROM org_master WHERE country = 'RS'").fetchone()[0]
    if actual != expected:
        raise RuntimeError(f"seed drift: expected {expected} RS orgs after bootstrap, found {actual}")
    return actual


def load_demo_listings(conn: psycopg.Connection, store: LocalFsStore) -> int:
    """Small, clearly-labelled OFFLINE demo data (packet's own D6 discipline: a demo value must be as
    clearly labelled as a missing one, never presented as if it came from a real ad) - every title gets a
    '[SAMPLE]' prefix and its own raw envelope is marked sample=true. Idempotent: re-ingested by its own
    stable ext_uid, a re-run refreshes rows rather than duplicating them. Never used by the test suite
    (tests/test_api.py builds its own fixture) and never deployed anywhere but localhost."""
    rows = json.loads(_DEMO_FIXTURE_PATH.read_text(encoding="utf-8"))["listings"]
    n = 0
    for r in rows:
        # found live testing this exact function (2026-09-24): a per-ITEM "_comment" annotation (the fixture
        # file already had one, on the cross-source-dedupe demo row) was never stripped before being passed
        # to upsert_listing(), which rejects any key it doesn't recognise - a genuinely fresh ajp-init would
        # have crashed the moment it reached that row. Every underscore-prefixed key is documentation, never
        # a real column, so all of them are dropped here, not just the one that happened to be hit first.
        r = {k: v for k, v in r.items() if not k.startswith("_")}
        # T26 (packet 015), found live re-verifying this fixture's own real rendering: a row with a real,
        # stated country_iso2 but no explicit location_class defaulted to the DB column's own 'unknown'
        # (its DEFAULT) rather than 'located' - correct for a genuinely null-country demo row (the fixture's
        # own remote entry sets this explicitly already) but wrong for every other, perfectly-located demo
        # row, which never bothered to restate the obvious. Every real connector's own classify_location()
        # would call this 'located' too - matched here rather than leaving the demo data looking like R2's
        # own policy was never applied to it.
        if r.get("country_iso2") and "location_class" not in r:
            r["location_class"] = "located"
        # T26/T27 (packet 015), found live sweeping the real DB for any listing missing apply_url (2026-09-28):
        # every real connector's own row builder falls back to the ad's own canonical url when nothing more
        # specific is stated, so EVERY real listing always has >=1 clickable link - but this demo-fixture
        # loader inserts each row's JSON verbatim and was never given the same fallback, so any demo row that
        # didn't already restate its own url as apply_url ended up with a real, persisted NULL apply_url
        # (found live: 5 of the fixture's rows, e.g. the PhD demo listing's own siblings). Matched to the real
        # connectors' own behaviour here rather than leaving demo data violate the very guarantee it's meant
        # to help verify.
        if "apply_url" not in r:
            r["apply_url"] = r.get("url")
        slug = r.pop("org_slug", None)
        if slug:
            org = conn.execute("SELECT id FROM org_master WHERE slug = %s", (slug,)).fetchone()
            if org:
                r["org_id"] = org[0]
        r["title_raw"] = f"[SAMPLE] {r['title_raw']}"
        ref = put_raw(store, kind="html", source_id=r["source_id"], url=r["url"],
                     content=f"<html><body>{r['title_raw']}</body></html>".encode(),
                     fetched_at=datetime.now(timezone.utc), status=200, robots_allowed=True,
                     extra={"sample": True})
        r["raw_html_ref"] = ref.key
        r.setdefault("ext_uid", r["url"])
        upsert_listing_deduped(conn, r, store)
        n += 1
    return n


def bootstrap_all(conn: psycopg.Connection, store: LocalFsStore, *, demo: bool = False) -> Dict[str, int]:
    """Everything a fresh install needs, in the right order, one call - what both tools/dev_up.py and
    ajp.cli.init actually run. `demo=False` (T32, packet 019: the real production default now) skips the
    sample listings entirely - a real production-facing init must never load fabricated [SAMPLE] ads by
    default. `demo=True` still works exactly as before and is never removed - tools/dev_up.py's own dev
    stack (and the Playwright flows.spec.ts suite, which depends on those fixture rows) keeps passing it
    explicitly; only the DEFAULT changed, for ajp-init/production bootstrap callers that don't ask either way."""
    n_base, n_supplement = seed_all_orgs(conn)
    ensure_sources(conn, store)
    seed_fx(conn)
    n_listings = load_demo_listings(conn, store) if demo else 0
    total_orgs = assert_org_count(conn)
    return {"orgs_base": n_base, "orgs_supplement": n_supplement, "orgs_total": total_orgs, "demo_listings": n_listings}
