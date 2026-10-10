"""T18: one detector, run against an organisation's own careers page HTML, that recognises which ATS (if
any) it links to and extracts the tenant/site id needed to route it to the right connector family - never
the other way around (we never guess a tenant; we only ever read one back out of a link the org itself
published). Unknown/no match -> the caller falls through to gen_wordpress/gen_sitemap_html discovery, then
deeplink (packet 009's own routing order).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class AtsMatch:
    family: str
    tenant: Optional[str] = None
    site: Optional[str] = None
    evidence_url: str = ""


# Order matters only for de-duplication (first match per family wins); every pattern is checked regardless.
_PATTERNS = {
    # {tenant}.{wdN}.myworkdayjobs.com/{locale/}{site} - packet 009/011: NEVER guess these, only read them
    # back from a real link (see ats_workday.py's own discovery-by-redirect for the case where no such link
    # exists on the page at all).
    "ats_workday_cxs": re.compile(
        r"https?://(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[A-Za-z0-9_-]+)",
        re.IGNORECASE),
    # ats_smartrecruiters: detection only (t18_ats_corrections - the keyless API is robots-denied for our
    # UA); tenant is still worth recording so a future employer-published feed/page can be found by it.
    "ats_smartrecruiters": re.compile(
        r"https?://(?:jobs|careers)\.smartrecruiters\.com/(?P<tenant>[A-Za-z0-9_.-]+)", re.IGNORECASE),
    "ats_teamtailor": re.compile(r"https?://(?P<tenant>[a-z0-9-]+)\.teamtailor\.com", re.IGNORECASE),
    "ats_reachmee_varbi": re.compile(r"https?://[a-z0-9.-]*\.(?:reachmee|varbi)\.com/[^\s\"'<>]*", re.IGNORECASE),
    "ats_successfactors": re.compile(r"https?://(?:career\d*|[a-z0-9-]+)\.successfactors\.com/[^\s\"'<>]*", re.IGNORECASE),
    "ats_taleo": re.compile(r"https?://[a-z0-9.-]*\.taleo\.net/[^\s\"'<>]*", re.IGNORECASE),
    "ats_icims": re.compile(r"https?://(?P<tenant>[a-z0-9-]+)\.icims\.com", re.IGNORECASE),
    "ats_greenhouse": re.compile(
        r"https?://(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?(?P<tenant>[A-Za-z0-9_-]+)",
        re.IGNORECASE),
    "ats_lever": re.compile(r"https?://jobs\.lever\.co/(?P<tenant>[A-Za-z0-9_-]+)", re.IGNORECASE),
    "ats_jobteaser": re.compile(r"https?://[a-z0-9.-]*\.jobteaser\.com/[^\s\"'<>]*", re.IGNORECASE),
}


def detect_ats(html: str, base_url: str = "") -> List[AtsMatch]:
    """Every distinct ATS family the page links to, each with the FIRST tenant/site found for it. A page
    can legitimately link to more than one (e.g. a generic 'other vacancies' link to a partner ATS) - callers
    decide which match to act on; this function only reports what it saw."""
    out: List[AtsMatch] = []
    for family, pattern in _PATTERNS.items():
        m = pattern.search(html)
        if not m:
            continue
        groups = m.groupdict()
        out.append(AtsMatch(family=family, tenant=groups.get("tenant"), site=groups.get("site"),
                            evidence_url=m.group(0)))
    return out


def detect_one(html: str, base_url: str = "") -> Optional[AtsMatch]:
    """The single best match, when a caller just wants "is this org on an ATS at all" - Workday/Greenhouse/
    Lever (the families we can actually ingest from) are preferred over detection-only families."""
    matches = detect_ats(html, base_url)
    if not matches:
        return None
    priority = ("ats_workday_cxs", "ats_greenhouse", "ats_lever", "ats_smartrecruiters", "ats_teamtailor",
               "ats_reachmee_varbi", "ats_successfactors", "ats_taleo", "ats_icims", "ats_jobteaser")
    by_family = {m.family: m for m in matches}
    for family in priority:
        if family in by_family:
            return by_family[family]
    return matches[0]
