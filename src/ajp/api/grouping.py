"""T14 schema_reconciliation.dedupe_view: pick a canonical source among a listing's ``sources[]`` by
trust rank (direct org > national board > aggregator), and format the array for the API response.

The listing row's own extracted fields (title_raw, org_raw, salary_*, ...) are never re-derived here -
T13's own accepted contract is that the FIRST-inserted row's fields are never overwritten by a later
merge (``upsert_listing_deduped``, tested in tests/test_cross_source_dedupe.py). "Canonical by trust
rank" is a read-time concern only: which source's own URL/label the UI should lead with (Apply button,
"verified direct org page" badge), independent of insertion order.

Rank is derived from what each connector actually IS, not guessed:
  1 direct_org      ``rs_org_boards``  - every Serbian institution's own employment board (board.py)
  2 national_board   ``nsz``           - Nacionalna sluzba za zaposljavanje, Serbia's national
                                          employment service (nsz.py) - the one connector that matches
                                          PM's own "national board" tier
  3 aggregator       academictransfer, jobs_ac_uk, nature_careers, cern, sljaka, cnrs, academicpositions
                                          - T13's tier-1 connectors
  9 unknown          anything else (a future source not yet classified here; never crashes, just sorts last)

Channel (deeplink) sources never appear in ``sources[]`` at all - ``upsert_listing``/``upsert_listing_deduped``
refuse to ingest from a 'channel' kind source (listing_rejects_channel_source trigger), so there is nothing
to rank for them; they surface only via GET /v1/channels.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DIRECT_ORG_SOURCE_ID = "rs_org_boards"
NATIONAL_BOARD_SOURCE_IDS = frozenset({"nsz"})
AGGREGATOR_SOURCE_IDS = frozenset({
    "academictransfer", "jobs_ac_uk", "nature_careers", "cern", "sljaka", "cnrs", "academicpositions",
    # T19: ku_dk/kth are bespoke, single-org connectors (Univ. of Copenhagen / KTH's own boards), same shape
    # as cern/sljaka above - same convention applies (a bespoke T13/T19-style connector is tier-3 'aggregator'
    # here regardless of whether it happens to be one org's own board; 'direct_org' stays reserved for
    # rs_org_boards specifically).
    "ku_dk", "kth", "inria",
})

_RANK = {DIRECT_ORG_SOURCE_ID: 1, **{s: 2 for s in NATIONAL_BOARD_SOURCE_IDS}, **{s: 3 for s in AGGREGATOR_SOURCE_IDS}}


def trust_rank(source_id: str) -> int:
    return _RANK.get(source_id, 9)


def is_verified_direct(source_id: str) -> bool:
    """The UI badge: 'verified direct org page' (filter_semantics.source)."""
    return source_id == DIRECT_ORG_SOURCE_ID


def canonical_source(sources: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The best-trust-ranked entry, ties broken by earliest first_seen. None only for an empty/malformed list."""
    if not sources:
        return None
    return min(sources, key=lambda s: (trust_rank(s.get("source", "")), s.get("first_seen") or ""))


# T18: every ATS family is a single ORGANISATION's own board, just hosted on a SaaS platform - trust-rank
# equivalent to a direct org board (rs_org_boards), not a third-party aggregator, regardless of which one
# specific tenant a given source row is for (each is its own per-tenant source_id, e.g.
# 'ats_workday_cxs__embl_embl', 'gen_wordpress__pefja' - never matched by a fixed literal set the way T13's
# aggregators are, since new tenants are added by config, not by code).
_DIRECT_ORG_ATS_FAMILIES = frozenset({"ats_workday_cxs", "ats_lever", "ats_greenhouse", "gen_wordpress"})
# gen_sitemap_html covers both a single org's own sitemap-driven board AND genuine multi-employer aggregators
# (its flagship live use, THEunijobs) - approximated as 'aggregator' by default; a future single-org config
# for this family would be mis-labelled by this approximation, not by anything ingestion-side.
_AGGREGATOR_ATS_FAMILIES = frozenset({"gen_sitemap_html"})


def route_type_label(source_id: str, ats_family: Optional[str] = None) -> str:
    """/v1/sources' 'route type' chip: which trust tier a source belongs to, in words."""
    if source_id == DIRECT_ORG_SOURCE_ID:
        return "direct_org"
    if source_id in NATIONAL_BOARD_SOURCE_IDS:
        return "national_board"
    if source_id in AGGREGATOR_SOURCE_IDS:
        return "aggregator"
    if ats_family in _DIRECT_ORG_ATS_FAMILIES:
        return "direct_org"
    if ats_family in _AGGREGATOR_ATS_FAMILIES:
        return "aggregator"
    return "channel_or_other"


def format_sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """DB shape {source,url,first_seen[,last_seen]} -> API shape {source_id,url,first_seen,last_seen,
    verified_direct}. last_seen falls back to first_seen for any row written before that field existed."""
    out = []
    for s in sources or []:
        out.append({
            "source_id": s.get("source"),
            "url": s.get("url"),
            "first_seen": s.get("first_seen"),
            "last_seen": s.get("last_seen") or s.get("first_seen"),
            "verified_direct": is_verified_direct(s.get("source", "")),
        })
    return out
