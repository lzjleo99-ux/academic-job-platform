"""T18 new governance rule (recon/t18_ats_corrections.json): a TDM/legal opt-out declared in a site's own
robots.txt comments or terms (EU DSM Art.4 / section-44b-UrhG style 'automated access prohibited without
permission') forces ``deeplink_only`` even when every path is technically robots-allowed - academics.com is
the live fixture case (verified 2026-09-23, exact text below). ``RobotsCache`` still parses the ALLOW/
DISALLOW directives in the very same file completely normally; this is a separate, additional check over the
same raw text, checked once per host and recorded on the source row's ``tdm_reservation`` column.

A 'declared' result must always be paired with an ``operator_actions.md`` entry (the ruling's own words) -
that pairing is enforced by convention/review, not a database constraint, since the two live in different
systems (an operator note is prose, not a DB row).
"""
from __future__ import annotations

import re

# Deliberately not one giant regex: each is a real, distinct legal phrasing pattern seen in the wild, matched
# independently so a robots.txt only needs to hit ONE of these to be flagged - never combined into a single
# fragile pattern that a paraphrase could slip past.
_UrhG_44b = re.compile(r"(§|section)\s*44\s*b\s*urhg", re.IGNORECASE)
_TDM_RESERVATION = re.compile(r"text\s+and\s+data\s+mining", re.IGNORECASE)
_PROHIBITED_WITHOUT_PERMISSION = re.compile(
    r"automated\s+(means|access).{0,150}?(prohibited|not\s+permitted).{0,150}?permission",
    re.IGNORECASE | re.DOTALL,
)


def detect_tdm_reservation(robots_text: str) -> str:
    """'declared' | 'none'. Comment lines (starting with '#') are exactly where these declarations tend to
    live in the wild - this scans the raw text regardless of the '#' prefix, since a legal notice does not
    stop being one for being written as a comment."""
    if not robots_text:
        return "none"
    if _UrhG_44b.search(robots_text):
        return "declared"
    if _TDM_RESERVATION.search(robots_text) and "permission" in robots_text.lower():
        return "declared"
    if _PROHIBITED_WITHOUT_PERMISSION.search(robots_text):
        return "declared"
    return "none"
