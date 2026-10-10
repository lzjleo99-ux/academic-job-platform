"""T17: the transparent, deterministic scoring formula (packet 008's own D4 standing ruling - every number
here must be reproducible from the stored matches, never a black box).

Two 'buckets' a verbatim match can fall into (never both counted for the same sentence): 'oa' - every
open-access source adapter (openalex/crossref/unpaywall/core/semantic_scholar/oa_fulltext) collectively, since
none of them individually promises meaningful coverage on its own; 'internal' - the user's own uploaded
reference corpus, kept separate because it is a fundamentally different (closed, user-controlled) comparison
set, not "the open web". Copyleaks (when enabled) is scored and shown entirely separately, with its own
provider-native score, never folded into this formula's own estimate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ajp.thesis.similarity.segment import Sentence

VERBATIM_CONTAINMENT_THRESHOLD = 0.7      # fraction of a sentence's own shingles found verbatim in a source
SENTENCE_EXACT_THRESHOLD = 0.95           # containment at/above this counts as the 'sentence_exact' signal
# 5-word shingle Jaccard is naturally low for realistic sentence lengths (~15-25 words -> ~10-20 shingles per
# sentence, so Jaccard moves in coarse steps) - calibrated empirically against a hand-written 'light
# paraphrase' fixture (tests/test_similarity_planted_duplication.py) that keeps a couple of short phrases
# intact while rewording the rest, which is what a genuine light paraphrase looks like; 0.25 (the original
# guess) missed it entirely.
PARAPHRASE_JACCARD_THRESHOLD = 0.10

SOURCE_BUCKET: Dict[str, str] = {
    "internal_corpus": "internal",
    "openalex": "oa", "crossref": "oa", "unpaywall": "oa", "core": "oa",
    "semantic_scholar": "oa", "oa_fulltext": "oa",
}

DISCLAIMERS = (
    "This is a heuristic similarity ESTIMATE against open-access sources and your uploaded references. "
    "It is not Turnitin/iThenticate and cannot see closed publisher databases or the full web.",
    "A low estimate does not prove originality; a high estimate requires manual review.",
)
COPYLEAKS_DISCLAIMER = ("Copyleaks results come from an external processor (their web+cloud index) under "
                        "their own terms of service; shown separately with provider attribution, never "
                        "folded into the estimate above.")


@dataclass
class SpanMatch:
    sentence_index: int
    start: int                            # offset into the thesis's own (reference-masked) full_text
    end: int
    token_count: int
    snippet: str
    source_id: str
    signal: str                           # 'verbatim' | 'paraphrase'
    score: float                          # containment ratio (verbatim) or Jaccard (paraphrase), 0..1
    source_url: Optional[str] = None
    title: Optional[str] = None
    attribution: Optional[str] = None


@dataclass
class SourceCoverage:
    source_id: str
    available: bool                       # False = disabled/no key/skipped entirely
    candidates_checked: int = 0
    matched: int = 0
    error: Optional[str] = None


@dataclass
class SimilarityReport:
    total_sentences: int
    total_tokens: int
    verbatim_oab_pct: float
    verbatim_internal_pct: float
    union_verbatim_pct: float
    sentence_exact_pct: float
    estimate_pct: float
    confidence_band: str                  # 'very_low' | 'low' | 'medium' | 'high'
    coverage_statement: str
    coverage: List[SourceCoverage]
    matched_spans: List[SpanMatch]
    paraphrase_spans: List[SpanMatch]
    copyleaks: Optional[Dict[str, Any]] = None
    disclaimers: List[str] = field(default_factory=lambda: list(DISCLAIMERS))


def _bucket_token_pct(best_by_sentence_bucket: Dict["tuple[int, str]", SpanMatch], bucket: str, total_tokens: int) -> float:
    toks = sum(m.token_count for (_, b), m in best_by_sentence_bucket.items() if b == bucket)
    return round(100 * toks / total_tokens, 2) if total_tokens else 0.0


def compute_report(sentences: List[Sentence], matches: List[SpanMatch], coverage: List[SourceCoverage],
                   copyleaks: Optional[Dict[str, Any]] = None) -> SimilarityReport:
    total_tokens = sum(len(s.text.split()) for s in sentences)
    verbatim = [m for m in matches if m.signal == "verbatim"]
    verbatim_sentence_idx = {m.sentence_index for m in verbatim}
    paraphrase = [m for m in matches if m.signal == "paraphrase" and m.sentence_index not in verbatim_sentence_idx]

    # best verbatim match per (sentence, bucket) - a sentence is never counted twice within the same bucket
    best_by_sentence_bucket: Dict["tuple[int, str]", SpanMatch] = {}
    for m in verbatim:
        bucket = SOURCE_BUCKET.get(m.source_id, "oa")
        key = (m.sentence_index, bucket)
        if key not in best_by_sentence_bucket or m.score > best_by_sentence_bucket[key].score:
            best_by_sentence_bucket[key] = m

    verbatim_oab_pct = _bucket_token_pct(best_by_sentence_bucket, "oa", total_tokens)
    verbatim_internal_pct = _bucket_token_pct(best_by_sentence_bucket, "internal", total_tokens)

    # union: best verbatim match per sentence across ALL buckets (a sentence matched in both buckets is
    # still only counted once here) - always >= max(oab, internal), reported alongside the max-pair estimate.
    best_by_sentence: Dict[int, SpanMatch] = {}
    for m in verbatim:
        if m.sentence_index not in best_by_sentence or m.score > best_by_sentence[m.sentence_index].score:
            best_by_sentence[m.sentence_index] = m
    union_tokens = sum(m.token_count for m in best_by_sentence.values())
    union_verbatim_pct = round(100 * union_tokens / total_tokens, 2) if total_tokens else 0.0

    exact_sentences = {m.sentence_index for m in verbatim if m.score >= SENTENCE_EXACT_THRESHOLD}
    sentence_exact_pct = round(100 * len(exact_sentences) / len(sentences), 2) if sentences else 0.0

    estimate_pct = max(verbatim_oab_pct, verbatim_internal_pct)
    estimate_pct = min(estimate_pct, union_verbatim_pct) if union_verbatim_pct else estimate_pct  # defensive cap

    contributing = sorted({c.source_id for c in coverage if c.matched > 0})
    configured = [c for c in coverage if c.available]
    if not contributing:
        confidence_band = "very_low"
    elif len(contributing) == 1:
        confidence_band = "low"
    elif len(contributing) <= 3:
        confidence_band = "medium"
    else:
        confidence_band = "high"

    coverage_statement = (
        f"{len(contributing)} of {len(configured)} enabled source(s) returned candidate matches "
        f"({', '.join(contributing) if contributing else 'none'}); the estimate is bounded to what those "
        f"sources could see - it is not a web-wide check."
    )

    disclaimers = list(DISCLAIMERS)
    if copyleaks is not None:
        disclaimers.append(COPYLEAKS_DISCLAIMER)

    return SimilarityReport(
        total_sentences=len(sentences), total_tokens=total_tokens, verbatim_oab_pct=verbatim_oab_pct,
        verbatim_internal_pct=verbatim_internal_pct, union_verbatim_pct=union_verbatim_pct,
        sentence_exact_pct=sentence_exact_pct, estimate_pct=estimate_pct, confidence_band=confidence_band,
        coverage_statement=coverage_statement, coverage=coverage,
        matched_spans=sorted(best_by_sentence.values(), key=lambda m: m.start),
        paraphrase_spans=sorted(paraphrase, key=lambda m: m.start), copyleaks=copyleaks, disclaimers=disclaimers,
    )
