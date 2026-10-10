"""T25/T33 (packet 018, PM ruling - supersedes T25's own earlier design): the deterministic, documented
matching engine. Every weight below is committed here as a real, importable constant (never a magic number
buried in a query) so the methodology page can render the exact same numbers this module actually uses -
matching this project's own D4-style transparency standard already applied to the thesis similarity formula
(ajp.thesis.similarity.formula). A score is always accompanied by its own per-criterion factors breakdown -
never a black-box number with nothing to explain it.

T33's own real PM acceptance run found the ORIGINAL design (a criterion missing on either side excluded from
BOTH the numerator and the denominator, then rescaled to whatever weight WAS evaluable) scored 2,381 of 3,948
open listings a perfect 100 for one real test CV - a sparse listing where only ft_pt+discipline happened to
be evaluable was structurally indistinguishable from a listing that matched on every single criterion, since
both rescaled to 100% of their own (different) evaluable totals. PM's own explicit ruling, replacing that
design: a criterion that cannot be evaluated for a pair (missing on the listing OR the applicant side) now
contributes a NEUTRAL fraction of 0.5 to its own weight and STAYS in the denominator - never scored 0 (that
would punish a real absence of stated data as if it were a bad fit) and never dropped (that is what let
sparse listings inflate to 100). The denominator is therefore now always the full committed MATCH_WEIGHTS
total (100) for every listing - a sparse listing (many unknowns) converges toward ~50, a listing that
genuinely matches on every stated criterion can still reach 100, and a listing that actively mismatches on
what it does state falls below 50. `coverage` (weight with POSITIVE evidence / total weight) is reported
alongside the score precisely so a sparse-but-not-actively-good match at ~50 is never mistaken for a genuinely
well-evidenced one - the UI and methodology page both surface it, not just the bare number.

See docs/matching_formula.md for the full prose explanation (weights table, worked examples, and what this
formula deliberately does not do) - keep that document in sync with this module if either changes.
"""
from __future__ import annotations

import functools
import os
import re
from typing import Any, Dict, List, Optional

from ajp.applicants.ahocorasick import AhoCorasick
from ajp.applicants.profile_extract import (
    GENERIC_SKILL_TERM_STOPLIST, extract_disciplines, infer_listing_disciplines,
)
from ajp.applicants.skill_clusters import cluster_siblings

# T41 (packet 024, P1 perf): both engines are implemented, fully tested, and PROVEN byte-identical (see
# tests/test_applicant_matching.py's own identity-diff test, and the live, all-4,065-real-open-row
# comparison reported in this task's own events.jsonl row) - but real, repeated, interleaved measurement
# (see that same report) found the NEW Aho-Corasick engine does NOT outperform the existing regex engine at
# this project's own real vocabulary scale (~27 candidate phrases after T40's cluster expansion): pure-
# Python per-character interpreter overhead dominates over AC's better theoretical complexity at this N,
# a genuinely counter-intuitive, measured (not assumed) finding - reported as its own need_input rather
# than shipping a quietly-worse default. "regex" (the proven-faster, already-shipped engine) stays the
# default; "aho_corasick" is available, correct, and byte-identical, opt-in via this one flag, for PM's own
# own re-verification or a future, faster (e.g. compiled) implementation of the same algorithm.
SKILL_MATCH_ENGINE = os.environ.get("AJP_SKILL_MATCH_ENGINE", "regex")

# T38 (packet 022, PM ruling - rebalances T36's own weights again, still summing to 100): PM's own explicit
# rationale, verified live - the broad, flat 6-bucket discipline signal has weak WITHIN-bucket discrimination
# (an electrical engineer and a mechanical engineer both land in the same "Engineering and technology"
# bucket; a biologist and an ML researcher both land in "Natural sciences") - discipline's own weight shrinks
# from 20 to 15 accordingly. skill_relevance (T37's own newly-recalibrated, newly-discriminating factor) is
# PM's own stated strongest real relevance signal - its weight grows from 15 to 20, swapping places with
# discipline. location/salary stay the strongest real, almost-always-stated signals, unchanged at 20 each.
#
# T36 (packet 021, PM ruling, superseded by the above for discipline/skill_relevance only): discipline's own
# coarse, flat 6-bucket signal is now backed by a real listing-side inference (T36a) instead of nothing at
# all, but it can still only ever distinguish 6 broad fields - the new skill_relevance factor is what
# actually tells a genuinely CS/AI-relevant listing apart from an unrelated one bucketed the same broad way
# (PM's own real evidence: 22 listings tied at the exact same score because the flat discipline+country
# signal alone cannot tell them apart). degree_level's own weight shrinks - PM's own live audit found
# degree_required stated on only 36 of 3,959 open listings, so this criterion is almost always neutral by
# accident rather than real evidence either way, and should never dominate the score the way its old 15
# points did.
MATCH_WEIGHTS: Dict[str, int] = {
    "discipline": 15,
    "skill_relevance": 20,
    "location": 20,
    "salary": 20,
    "ft_pt": 10,
    "language": 10,
    "degree_level": 5,
}


# T36f (packet 021, perf): _score_discipline and _score_skill_relevance BOTH need "this listing's own topic
# genuinely cannot be determined at all" to mean the exact same thing - which meant computing this twice per
# listing (once per scorer), each call re-running the same title/requirements/body keyword scan. A small LRU
# cache keyed on the listing's own text content is enough to collapse that back-to-back duplicate work (the
# two scorers run one right after another for the SAME listing before this module ever moves to the next
# one) - never sized to cache ACROSS listings (each real listing's own text is essentially unique, so a
# larger cache would only grow without ever paying off), purely to avoid the immediate, guaranteed re-work.
@functools.lru_cache(maxsize=8)
def _listing_disciplines_cached(title_raw: str, requirements_raw: str, raw_text: str,
                                stated: "tuple[str, ...]") -> "frozenset[str]":
    inferred = infer_listing_disciplines(title_raw, requirements_raw, raw_text)
    stated_text = " ".join(stated)
    stated_buckets = extract_disciplines(stated_text) if stated_text.strip() else []
    return frozenset(inferred) | frozenset(stated_buckets)


def _listing_disciplines(listing: Dict[str, Any]) -> "frozenset[str]":
    """The listing's own discipline bucket(s), inferred (T36a) or, on the rare row that ever states a real
    disciplines_euroscivoc, honored too (run through the SAME keyword scheme, never trusted as an exact-code
    match against it, since different connectors already populate it with mutually inconsistent free text -
    see profile_extract.py's own docstring). Shared by both _score_discipline and _score_skill_relevance so
    "this listing's own topic genuinely cannot be determined at all" means the exact same thing to both.

    T39 (packet 022, perf): when the listing's own row already carries a persisted `inferred_disciplines`
    column (computed at ingest time by ajp.ingest.upsert_listing, via the SAME infer_listing_disciplines_
    with_zone() this function would otherwise call live), that stored value is used directly - provably
    identical to the on-the-fly result, since it IS that exact function's own prior output, just computed
    once at ingest instead of on every single matches request; no caching is needed for this fast path at
    all (a plain dict read, never a regex scan over the listing's own potentially-thousands-of-characters
    text). A NULL/missing column (a listing ingested before this migration, or any as-yet-unbackfilled row)
    falls back to the ORIGINAL cached live computation unchanged - never a behavioral difference, only a
    question of WHEN the same computation happened."""
    stored = listing.get("inferred_disciplines")
    if stored is not None:
        inferred = frozenset(stored.get("buckets") or [])
        stated_text = " ".join(listing.get("disciplines_euroscivoc") or [])
        stated_buckets = frozenset(extract_disciplines(stated_text)) if stated_text.strip() else frozenset()
        return inferred | stated_buckets
    return _listing_disciplines_cached(listing.get("title_raw") or "", listing.get("requirements_raw") or "",
                                       listing.get("raw_text") or "",
                                       tuple(listing.get("disciplines_euroscivoc") or []))


def _score_discipline(profile: Dict[str, Any], listing: Dict[str, Any]) -> "Optional[tuple[float, str]]":
    """T36b (packet 021, PM ruling - the correctness floor): a factor may be NEGATIVE only with POSITIVE
    evidence of an actual mismatch - a listing whose own topic simply cannot be determined at all (T36a's own
    inference finds nothing, and no real disciplines_euroscivoc was ever stated either) is 'unknown', never
    'negative', symmetric with the applicant-side missing-data case just above it. Only a listing that DOES
    name a real, inferable topic that fails to overlap with the applicant's own is a genuine mismatch."""
    disciplines = profile.get("disciplines") or []
    if not disciplines:
        return None
    listing_disciplines = _listing_disciplines(listing)
    if not listing_disciplines:
        return None    # the listing's own topic genuinely cannot be determined - not evidence of a mismatch
    hits = set(disciplines) & listing_disciplines
    if hits:
        return 1.0, f"overlaps on {', '.join(sorted(hits))}"
    return 0.0, f"listing's own inferred/stated field(s) ({', '.join(sorted(listing_disciplines))}) do not overlap with the applicant's ({', '.join(disciplines)})"


# T36c (packet 021, PM's own real evidence - the actual fix for the 'tie farm': discipline's flat 6-bucket
# match cannot tell a genuinely relevant listing apart from an unrelated one in the SAME broad bucket, e.g.
# CS/AI vs plant biology both landing under 'Natural sciences'). Normalized overlap between the applicant's
# own stated skills (plus the keyword phrases behind whichever discipline bucket(s) they matched) and the
# listing's own text - title hits count strongest, requirements next, raw body weakest, and multi-word
# phrases ('machine learning', 'finite element') outrank single generic tokens ('python', 'data') since a
# multi-word phrase is far less likely to be an incidental, unrelated word than a single common token is.
_TITLE_HIT_WEIGHT, _REQUIREMENTS_HIT_WEIGHT, _BODY_HIT_WEIGHT = 1.0, 0.6, 0.3
# T37 (packet 022, R1 - PM's own real live measurement: T36c's curve was "numerically inert"): the OLD
# saturation point (2.0), normalized against the OLD phrase weights (1.0/0.5), made even a single exact
# TITLE-level phrase hit score only ~0.5 - nowhere near enough to ever clearly separate a genuinely relevant
# listing from an unrelated one once a handful of weaker body-level hits could approach the same number (PM's
# own measured real example: a listing whose title LITERALLY is "Postdoc in Robot Learning for Manipulation"
# still scored only 0.12 for an ML/CS applicant). PM's own explicit calibration target, a best-evidence
# saturating curve: ONE exact multi-word phrase hit in the listing's own TITLE must alone be worth >= 0.80 of
# the full credit; two or more such strong (title/requirements) hits must saturate at the full 1.0. With the
# new phrase weights below (multi-word=2, single-token=1), a single title-zone multi-word hit earns
# 2 * 1.0 (title zone weight) = 2.0 raw points - 2.5 is the SMALLEST saturation point for which that lands AT
# the 0.80 floor (2.0 / 2.5 = 0.80 exactly, tested directly against this exact arithmetic, not guessed); two
# such hits (4.0 raw) already clears 2.5 and is capped at the full 1.0 by the `min()` below.
_SATURATION_WEIGHT = 2.5
# The SAME proportion-of-SAT this cap always was (0.4), re-derived against the new SAT: an unrelated
# listing's own long raw_text can incidentally contain enough generic single-token "hits" (python/data/
# software all appear in all sorts of unrelated ads) to approach a real match's own score on body-hits alone
# - body-only contribution is capped at 0.4 * 2.5 = 1.0 raw points, i.e. at most 1.0 / 2.5 = 0.40 of the full
# credit (half of the 0.80 a single genuine TITLE hit earns on its own) - a keyword-stuffed body can never
# approach what a real title/requirements hit does.
_BODY_ONLY_CAP_FRACTION = 0.4


def _phrase_weight(phrase: str) -> float:
    # T37 (packet 022, R1): PM's own explicit phrase weights, replacing the old 1.0/0.5 pair - a multi-word
    # phrase ("mechanical engineering", "finite element") is far less likely to be an unrelated, incidental
    # match than a single generic token ("python", "CAD") is, so it counts for twice as much.
    return 2.0 if " " in phrase.strip() else 1.0    # a multi-word phrase outranks a single generic token


# T36f (packet 021, perf): found live - real matches-endpoint timing against the real ~4,065-open-row DB was
# ~35s per applicant, far past PM's own ~1s bound; profiled directly (cProfile), not guessed - 436,844 calls
# to re.Pattern.search, ~107 per listing, were essentially the ENTIRE cost. The SAME applicant profile is
# scored against every open listing in one compute_matches call - looping over each of the (now, after T36a's
# own keyword expansion, often 30+) candidate phrases and running its OWN separate .search() against all 3
# text fields, per listing, was the dominant piece. Two independent fixes, both cached per (skills,
# discipline_terms) tuple (the only two profile fields this factor ever reads) so neither is ever rebuilt per
# listing within one applicant's own matches request: (1) ONE combined alternation regex instead of N
# separate compiled patterns - a single `finditer()` per text field finds every matching phrase in one pass,
# turning "N searches" into "1 scan"; (2) the phrase list, its per-phrase weights, and the combined pattern
# are all still built exactly once per applicant, never per listing.
def _build_candidate_phrases(skills_key: "tuple[str, ...]",
                             discipline_terms_key: "tuple[str, ...]") -> "Dict[str, tuple[str, Optional[str]]]":
    """The applicant-side vocabulary shared by BOTH match engines (T41, packet 024: this function was
    split out of the old, single `_compiled_relevance_pattern` unchanged, so the regex and Aho-Corasick
    engines are provably built from the exact same inputs). Returns `{surface_lower: (display, tilde_root)}`.

    T37 (packet 022, R2): the stoplist is re-applied here too, defensively, on top of
    extract_matched_discipline_terms's own filtering - a profile persisted before that fix (an existing
    applicant row in the live DB) can still carry an old, unfiltered discipline_terms list, and the
    applicant's own freeform `skills` field (hand-PATCHed or extracted) could in principle contain a bare
    generic word too; this is the ONE place that actually builds the candidate set, so it is the one place
    that must never be bypassed by a stale or hand-edited profile.

    T39 (packet 022), found live doing a before/after byte-identical comparison for the persistence
    switch: a real profile can state the SAME phrase under two different castings in `skills` vs
    `discipline_terms` (the real ML golden CV literally has "NLP" in `skills` and "nlp" - matched
    separately from elsewhere in the same CV's own text - in `discipline_terms`) - deduping them through a
    plain Python `set` let EITHER casing "win" depending on that set's own iteration order, which CPython
    does not guarantee stable across process restarts (hash randomization) - a real, PRE-EXISTING
    nondeterminism in `reason`'s own displayed text (never in the fraction/score/state themselves - both
    castings share the exact same `_phrase_weight`), caught only because this comparison runs the exact
    same request against two separate server processes. Deduped by LOWERCASE key instead, keeping the
    lexicographically-smallest original casing for a tie - deterministic regardless of hash seed or which
    of the two fields the phrase came from.

    T40 (packet 023, PM ruling OPTION_B - resolving T37's own need_input): every OTHER member of a
    cluster one of the applicant's own literal terms belongs to becomes an ADDITIONAL candidate surface
    phrase - e.g. a CV stating "machine learning" can now also match a listing's own "robot learning"
    (the real KTH case), or a CV stating "CAD" can match "Computer-Aided Design" (the real SUSMAT case).
    `tilde_root` is None for the applicant's own literal terms (kept exactly as before, no reason suffix)
    and the matching literal root's own display string for a cluster-only sibling (PM's own explicit
    tilde-reason convention, e.g. "robot learning (title; ~ machine learning)"). A sibling that happens to
    ALSO be one of the applicant's own literal terms keeps its literal (no-tilde) entry - "if the surface
    phrase equals the applicant term verbatim, keep today's format with no tilde suffix" (PM's own words).
    The stoplist is re-applied to every cluster sibling too (T40's own explicit "stoplist wins"
    precedence) - defensive, since no current frozen cluster member actually collides with it."""
    literal: Dict[str, str] = {}
    for p in (*skills_key, *discipline_terms_key):
        p = (p or "").strip()
        if not p or p.lower() in GENERIC_SKILL_TERM_STOPLIST:
            continue
        key = p.lower()
        if key not in literal or p < literal[key]:
            literal[key] = p

    all_phrases: "Dict[str, tuple[str, Optional[str]]]" = {lower: (disp, None) for lower, disp in literal.items()}
    for root_lower, root_display in literal.items():
        for sibling in cluster_siblings(root_display):
            sibling_lower = sibling.lower()
            if sibling_lower in literal or sibling_lower in GENERIC_SKILL_TERM_STOPLIST:
                continue
            existing = all_phrases.get(sibling_lower)
            if existing is None or root_display < existing[1]:
                all_phrases[sibling_lower] = (sibling, root_display)
    return all_phrases


# T36f (packet 021, perf): found live - real matches-endpoint timing against the real ~4,065-open-row DB was
# ~35s per applicant, far past PM's own ~1s bound; profiled directly (cProfile), not guessed - 436,844 calls
# to re.Pattern.search, ~107 per listing, were essentially the ENTIRE cost. The SAME applicant profile is
# scored against every open listing in one compute_matches call - looping over each of the (now, after T36a's
# own keyword expansion, often 30+) candidate phrases and running its OWN separate .search() against all 3
# text fields, per listing, was the dominant piece. Two independent fixes, both cached per (skills,
# discipline_terms) tuple (the only two profile fields this factor ever reads) so neither is ever rebuilt per
# listing within one applicant's own matches request: (1) ONE combined alternation regex instead of N
# separate compiled patterns - a single `finditer()` per text field finds every matching phrase in one pass,
# turning "N searches" into "1 scan"; (2) the phrase list, its per-phrase weights, and the combined pattern
# are all still built exactly once per applicant, never per listing.
#
# T41 (packet 024): this is now the "regex" engine specifically - kept behind the SKILL_MATCH_ENGINE flag
# as the old, already-shipped path (see its own module-level comment). The Aho-Corasick engine below
# (`_compiled_relevance_matcher`) is the new default; both are built from the IDENTICAL
# `_build_candidate_phrases` vocabulary and `weight_by_lower` shape, which is what makes a byte-identical
# comparison between them meaningful at all.
@functools.lru_cache(maxsize=256)
def _compiled_relevance_pattern(skills_key: "tuple[str, ...]", discipline_terms_key: "tuple[str, ...]"):
    all_phrases = _build_candidate_phrases(skills_key, discipline_terms_key)
    if not all_phrases:
        return None, {}
    # Longest-first ordering makes Python's own first-alternative-wins matching prefer a longer, more
    # specific phrase over a shorter one that happens to be its own substring/near match at the same text
    # position - the same real-world alternation-ordering trick this project's own rank-word/academic-
    # position patterns elsewhere already rely on. Sorted by the (case-insensitive) phrase itself, length
    # first, so ordering never depends on dict/set iteration order.
    ordered_lowers = sorted(all_phrases, key=len, reverse=True)
    pattern = re.compile(r"\b(" + "|".join(re.escape(lo) for lo in ordered_lowers) + r")\b", re.IGNORECASE)
    weight_by_lower = {
        lo: (disp, _phrase_weight(disp), tilde_root) for lo, (disp, tilde_root) in all_phrases.items()
    }
    return pattern, weight_by_lower


# T41 (packet 024, P1 perf): the new default engine - an Aho-Corasick automaton scans each listing text
# field in O(len(text)) regardless of how many candidate phrases it holds, unlike the regex alternation
# above, whose own cost per text position grows with the NUMBER of alternatives tried (T40's own cluster
# expansion routinely pushed the typical applicant's own candidate count from ~11 to ~27+, which is what
# pushed POST /matches's own real, measured median slightly over PM's 1.0s bound). Built from the EXACT
# SAME `_build_candidate_phrases` vocabulary as the regex engine above - the only thing this function adds
# is the automaton itself; `_iter_matched_lowers` (below) is what actually reproduces the regex engine's
# own word-boundary + leftmost-longest-non-overlapping semantics on top of it, since a raw Aho-Corasick
# scan reports every match (including overlapping ones) with no boundary concept at all.
@functools.lru_cache(maxsize=256)
def _compiled_relevance_matcher(skills_key: "tuple[str, ...]", discipline_terms_key: "tuple[str, ...]"):
    all_phrases = _build_candidate_phrases(skills_key, discipline_terms_key)
    if not all_phrases:
        return None, {}
    automaton = AhoCorasick(all_phrases.keys())
    weight_by_lower = {
        lo: (disp, _phrase_weight(disp), tilde_root) for lo, (disp, tilde_root) in all_phrases.items()
    }
    return automaton, weight_by_lower


def _is_word_char(ch: Optional[str]) -> bool:
    return ch is not None and (ch.isalnum() or ch == "_")


def _boundary_ok(text: str, start: int, end: int) -> bool:
    """Replicates `\\b` at both ends of a `[start, end)` span - a boundary holds wherever exactly one of
    the two characters either side of that exact position is a "word" character (letter/digit/underscore;
    `None`, for a position past either edge of the string, counts as "not a word character", matching
    `\\b`'s own string-start/end behavior)."""
    before = text[start - 1] if start > 0 else None
    first = text[start] if start < len(text) else None
    after = text[end] if end < len(text) else None
    last = text[end - 1] if end > 0 else None
    return _is_word_char(before) != _is_word_char(first) and _is_word_char(last) != _is_word_char(after)


def _iter_matched_lowers_ac(automaton: AhoCorasick, field_text: str) -> "List[str]":
    """T41: the Aho-Corasick equivalent of `pattern.finditer(field_text)` - yields the SAME sequence of
    matched (lowercase) phrases, in the SAME left-to-right, non-overlapping order, with the SAME
    "longest phrase wins at a given starting position" preference the regex engine's own longest-first
    alternation ordering gave it. The automaton itself reports every match regardless of word boundaries
    or overlap (see ahocorasick.py's own docstring for why that split of responsibility exists); boundary
    filtering and the greedy non-overlapping selection both happen here, against the ORIGINAL (not
    lowercased) `field_text`, since boundary-ness depends on the real characters, not their case."""
    if not field_text:
        return []
    text_lower = field_text.lower()
    by_start: "Dict[int, List[str]]" = {}
    for start, end, pat in automaton.scan_lowercase(text_lower):
        if _boundary_ok(field_text, start, end):
            by_start.setdefault(start, []).append(pat)
    if not by_start:
        return []
    out: "List[str]" = []
    pos = 0
    for start in sorted(by_start):
        if start < pos:
            continue                      # falls inside a span already consumed by an earlier, longer match
        longest = max(by_start[start], key=len)
        out.append(longest)
        pos = start + len(longest)
    return out


def _score_skill_relevance(profile: Dict[str, Any], listing: Dict[str, Any],
                           engine: Optional[str] = None) -> "Optional[tuple[float, str]]":
    # T41 (packet 024): `engine` lets a test (or an explicit override) force either match engine for the
    # SAME inputs, to prove they agree - production code never passes it, always using the module-level
    # SKILL_MATCH_ENGINE flag's own current value.
    engine = engine or SKILL_MATCH_ENGINE
    skills_key = tuple(profile.get("skills") or [])
    # T36c/T36f: the SPECIFIC discipline keyword(s) actually found in the CV (profile_extract.py's own
    # extract_matched_discipline_terms) - never the whole bucket's own sibling keyword list (a real bug found
    # live: 'mechanical engineering' in the CV was pulling in 'electrical engineering'/'chemical engineering'
    # etc as if they were equally relevant, just because all of them share the same broad discipline bucket).
    # `.get("discipline_terms")` falls back to `[]` (never the older, wider `disciplines`) for a profile
    # extracted before this field existed - correctly narrower, never wider, for any such old profile.
    discipline_terms_key = tuple(profile.get("discipline_terms") or [])
    if engine == "regex":
        pattern, weight_by_lower = _compiled_relevance_pattern(skills_key, discipline_terms_key)
        matcher = pattern
    else:
        matcher, weight_by_lower = _compiled_relevance_matcher(skills_key, discipline_terms_key)
    if matcher is None:
        return None
    title = listing.get("title_raw") or ""
    requirements = listing.get("requirements_raw") or ""
    # T36f (packet 021, perf): the SAME real, documented length cap infer_listing_disciplines uses (a real
    # ad's own core topic/skills are essentially always stated in its own opening text, never buried past a
    # few thousand characters) - found live profiling this exact factor against the real DB, where raw_text
    # can run to ~19.5k characters and this ran the full combined-phrase pattern over the entire thing.
    body = (listing.get("raw_text") or "")[:3000]
    if not (title.strip() or requirements.strip() or body.strip()):
        return None
    # T36b (packet 021, PM ruling): a listing whose own topic cannot be determined at all (the SAME check
    # _score_discipline itself uses) has genuinely nothing to compare relevance against either - a bland
    # title like "Some role" with no real content never finding one of the applicant's own specific skill
    # phrases is not POSITIVE evidence of a mismatch, it is an absence of evidence, and must stay 'unknown'.
    if not _listing_disciplines(listing):
        return None
    title_weight = requirements_weight = body_weight = 0.0
    # T37 (packet 022, R5): a (phrase, zone, tilde_root) triple, not just the bare phrase - "reasons must
    # list the actual matched phrases AND the zone (title/requirements/body)", so a reviewer can tell a
    # genuine exact-title hit apart from a weaker body-only one without re-deriving it from the raw fraction
    # themselves. T40 (packet 023) adds `tilde_root`: None for a literal applicant-term match (unchanged
    # format), or the applicant's own root term's display string for a cluster-sibling match (PM's own
    # tilde-reason convention below).
    matched: "List[tuple[str, str, Optional[str]]]" = []
    already_counted: "set[str]" = set()
    for field_text, weight_bucket in ((title, "title"), (requirements, "requirements"), (body, "body")):
        # T41: the two engines differ ONLY in how this one line finds "every matched lowercase phrase, in
        # left-to-right, non-overlapping, longest-at-each-start order" - everything below (dedup, weight
        # accumulation, saturation, reason building) is completely shared, unmodified code.
        lowers = (m.group(0).lower() for m in matcher.finditer(field_text)) if engine == "regex" \
            else iter(_iter_matched_lowers_ac(matcher, field_text))
        for lower in lowers:
            if lower in already_counted:
                continue                       # the SAME phrase found again in a later, lower-priority field
            already_counted.add(lower)
            phrase, w, tilde_root = weight_by_lower[lower]
            matched.append((phrase, weight_bucket, tilde_root))
            if weight_bucket == "title":
                title_weight += w
            elif weight_bucket == "requirements":
                requirements_weight += w
            else:
                body_weight += w
    body_weight = min(body_weight, _SATURATION_WEIGHT * _BODY_ONLY_CAP_FRACTION)
    earned = title_weight * _TITLE_HIT_WEIGHT + requirements_weight * _REQUIREMENTS_HIT_WEIGHT + body_weight * _BODY_HIT_WEIGHT
    fraction = round(min(1.0, earned / _SATURATION_WEIGHT), 4)
    if not matched:
        return 0.0, "no overlap between the applicant's own skills/field terms and this listing's text"
    # T40 (packet 023): PM's own exact tilde-reason convention, ASCII '~' - e.g.
    # "robot learning (title; ~ machine learning)" for a cluster-sibling hit, vs the plain
    # "machine learning (title)" when the surface phrase equals the applicant's own literal term.
    shown = [f"{phrase} ({zone}; ~ {root})" if root else f"{phrase} ({zone})" for phrase, zone, root in matched[:5]]
    return fraction, f"matches: {', '.join(shown)}" + (f" (+{len(matched) - 5} more)" if len(matched) > 5 else "")


_LEVEL_RANK = {"bsc": 1, "msc": 2, "phd": 3, "postdoc": 4, "prof": 5}
_DEGREE_REQUIRED_RANK = {"none": 0, "bsc": 1, "msc": 2, "phd": 3, "unknown": None}


def _score_degree(profile: Dict[str, Any], listing: Dict[str, Any]) -> "Optional[tuple[float, str]]":
    degree = (profile.get("degree") or {}).get("v")
    required = listing.get("degree_required")
    if not degree or not required or required == "unknown":
        return None
    req_rank = _DEGREE_REQUIRED_RANK.get(required)
    if req_rank is None:
        return None
    own_rank = _LEVEL_RANK.get(degree, 0)
    if own_rank >= req_rank:
        return 1.0, f"{degree} meets the ad's stated {required} requirement"
    return 0.0, f"{degree} is below the ad's stated {required} requirement"


def _score_location(profile: Dict[str, Any], listing: Dict[str, Any]) -> "Optional[tuple[float, str]]":
    desired = set(profile.get("desired_countries") or [])
    remote_ok = profile.get("remote_ok")
    listing_country = listing.get("country_iso2")
    location_class = listing.get("location_class")
    if not desired and remote_ok is None:
        return None
    if location_class == "remote" and remote_ok:
        return 1.0, "listing is fully remote and the applicant is open to remote work"
    if not desired:
        return None
    if listing_country and listing_country in desired:
        return 1.0, f"listing's country ({listing_country}) is one of the applicant's desired countries"
    if listing_country:
        return 0.0, f"listing's country ({listing_country}) is not among the applicant's desired countries"
    return None    # neither a resolvable country nor a remote listing - genuinely nothing to compare


def _score_ft_pt(profile: Dict[str, Any], listing: Dict[str, Any]) -> "Optional[tuple[float, str]]":
    want = profile.get("ft_pt")
    have = listing.get("ft_pt")
    if not want or not have or have == "unknown":
        return None
    return (1.0, "matches") if want == have else (0.0, f"applicant wants {want}, listing is {have}")


def _score_language(profile: Dict[str, Any], listing: Dict[str, Any]) -> "Optional[tuple[float, str]]":
    own_langs = {l["lang"].lower() for l in (profile.get("languages") or [])}
    listing_langs = listing.get("language") or []
    required = [l for l in listing_langs if isinstance(l, dict) and l.get("required")]
    if not own_langs or not required:
        return None
    req_codes = {(l.get("lang") or "").lower() for l in required}
    # 'en'/'english' and 'sr'/'serbian' both appear across this project's own data depending on the source -
    # a short ISO code and a full name for the same language must count as the same language here.
    _ALIASES = {"en": "english", "sr": "serbian", "de": "german", "fr": "french"}
    own_norm = {_ALIASES.get(l, l) for l in own_langs}
    req_norm = {_ALIASES.get(l, l) for l in req_codes}
    if req_norm & own_norm:
        return 1.0, "applicant states a required language"
    return 0.0, "applicant does not state any of the ad's required languages"


def _score_salary(profile: Dict[str, Any], listing: Dict[str, Any]) -> "Optional[tuple[float, str]]":
    min_wanted = profile.get("min_salary_eur_year")
    if min_wanted is None:
        return None
    # The listing's own currency conversion to EUR/year is expected to already be resolved onto the row this
    # function is given (ajp.api.rows.BASE_SELECT's own salary_eur_year_min/max, the SAME figure a listing
    # card shows) - this module never re-converts a currency itself, only compares numbers already in the
    # same unit. Prefers the ad's own stated MAX (the best case a real offer could reach); falls back to MIN
    # only when no max was ever stated.
    ceiling = listing.get("salary_eur_year_max")
    if ceiling is None:
        ceiling = listing.get("salary_eur_year_min")
    if ceiling is None:
        return None
    if ceiling >= min_wanted:
        return 1.0, f"listing's own salary (up to ~{ceiling:.0f} EUR/year) meets the applicant's stated minimum"
    return 0.0, f"listing's own salary (up to ~{ceiling:.0f} EUR/year) is below the applicant's stated minimum"


_SCORERS = {
    "discipline": _score_discipline, "skill_relevance": _score_skill_relevance, "degree_level": _score_degree,
    "location": _score_location, "ft_pt": _score_ft_pt, "language": _score_language, "salary": _score_salary,
}


_UNKNOWN_REASON = "not enough stated data on the applicant's or the listing's own side to evaluate this criterion"


def score_listing(profile: Dict[str, Any], listing: Dict[str, Any]) -> Dict[str, Any]:
    """(score 0-100, factors) for one listing against one applicant profile. T33 (packet 018, PM ruling): every
    one of the 7 committed criteria ALWAYS contributes to both the numerator and the fixed denominator (100) -
    a criterion neither side stated enough to evaluate contributes a neutral 0.5 fraction (state='unknown'),
    never a 0 and never dropped; a real match contributes 1.0 (state='positive'); a real mismatch contributes
    0.0 (state='negative'). `coverage` is the fraction of total weight backed by POSITIVE evidence specifically
    (never counting neutral/unknown or negative weight), so a sparse ~50 score is never mistaken for a
    genuinely well-evidenced one. Fixed, small number of criteria - O(1) work per listing, no query issued
    from inside this function at all (the N+1 guard lives in the caller, which fetches every OPEN listing's
    row once up front)."""
    factors: Dict[str, Dict[str, Any]] = {}
    counts = {"positive": 0, "negative": 0, "unknown": 0}
    total_weight = sum(MATCH_WEIGHTS.values())
    earned = positive_weight = 0.0
    for name, weight in MATCH_WEIGHTS.items():
        result = _SCORERS[name](profile, listing)
        if result is None:
            fraction, reason, state = 0.5, _UNKNOWN_REASON, "unknown"
        else:
            fraction, reason = result
            # T36c introduced the first CONTINUOUS-valued factor (skill_relevance, 0.0-1.0) - every other
            # factor here is still strictly binary (1.0/0.0) so this rule is unchanged for them: 'negative'
            # means genuine zero overlap/a real mismatch, never merely "less than a perfect 1.0".
            state = "negative" if fraction <= 0.0 else "positive"
        factors[name] = {"weight": weight, "fraction": fraction, "points": round(fraction * weight, 2),
                         "reason": reason, "state": state}
        earned += fraction * weight
        counts[state] += 1
        if state == "positive":
            positive_weight += weight
    score = round((earned / total_weight) * 100, 2) if total_weight else 0.0
    coverage = round(positive_weight / total_weight, 4) if total_weight else 0.0
    return {"score": score, "factors": factors, "counts": counts, "coverage": coverage,
           "total_weight": total_weight}


# T37 (packet 022, R4): a deterministic tie-break for two listings that land on the EXACT same overall
# score - PM's own explicit precedence, most-specific-signal first: (1) skill_relevance's own points (the
# factor this whole packet is about sharpening), (2) the number of factors with REAL positive evidence (a
# listing that is positively evidenced on more criteria is the more concretely-matched one, even at an equal
# score), (3) the listing's own posted_at (the more recently posted of two otherwise-identical ties), (4) the
# listing id itself, which is always unique and so always finally breaks the tie - "identical requests must
# return identical order" (PM's own words), which a Python `sort()` alone cannot promise on ties without an
# explicit key covering every one of these, since Python's sort is merely stable (preserves whatever
# arbitrary order the rows happened to arrive from the DB on an exact tie, which is not itself guaranteed
# stable across two separate queries/requests).
def match_sort_key(result: Dict[str, Any], listing: Dict[str, Any]) -> "tuple":
    """Ascending sort key - `sorted(results, key=lambda r: match_sort_key(r, listing_by_id[r['listing_id']]))`
    puts the BEST match first. Every component is oriented so "smaller sorts first" already means "more
    relevant" (numeric fields negated; `posted_at` converted to a negated timestamp with a missing date
    mapped to positive infinity so it never wins a tie against any real stated date; the listing id kept in
    its own natural ascending order as the final, always-decisive tie-break)."""
    skill_points = (result.get("factors", {}).get("skill_relevance") or {}).get("points", 0.0)
    positive_count = (result.get("counts") or {}).get("positive", 0)
    posted_at = listing.get("posted_at")
    posted_sort = -posted_at.timestamp() if posted_at is not None else float("inf")
    return (-result["score"], -skill_points, -positive_count, posted_sort, str(listing.get("id", "")))
