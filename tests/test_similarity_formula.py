"""T17 acceptance: 'formula unit tests with hand-computed expected percentages; confidence bands change
correctly when sources are disabled'.
"""
from ajp.thesis.similarity.formula import SourceCoverage, SpanMatch, compute_report
from ajp.thesis.similarity.segment import Sentence


def _sentences():
    # token counts: 10, 8, 12, 6 -> total 36
    return [
        Sentence(text=" ".join(["w"] * 10), start=0, end=10),
        Sentence(text=" ".join(["w"] * 8), start=10, end=18),
        Sentence(text=" ".join(["w"] * 12), start=18, end=30),
        Sentence(text=" ".join(["w"] * 6), start=30, end=36),
    ]


def test_hand_computed_percentages():
    sentences = _sentences()
    matches = [
        SpanMatch(sentence_index=0, start=0, end=10, token_count=10, snippet="s0",
                  source_id="internal_corpus", signal="verbatim", score=0.90),
        SpanMatch(sentence_index=1, start=10, end=18, token_count=8, snippet="s1",
                  source_id="openalex", signal="verbatim", score=0.99),
        SpanMatch(sentence_index=2, start=18, end=30, token_count=12, snippet="s2",
                  source_id="crossref", signal="paraphrase", score=0.30),
        # sentence 3: no match at all
    ]
    coverage = [
        SourceCoverage(source_id="internal_corpus", available=True, candidates_checked=1, matched=1),
        SourceCoverage(source_id="openalex", available=True, candidates_checked=5, matched=1),
        SourceCoverage(source_id="crossref", available=True, candidates_checked=5, matched=1),
        SourceCoverage(source_id="core", available=True, candidates_checked=5, matched=0),
    ]
    report = compute_report(sentences, matches, coverage)

    assert report.total_tokens == 36
    # verbatim_oab: only sentence 1 (openalex, 'oa' bucket) = 8/36
    assert report.verbatim_oab_pct == round(100 * 8 / 36, 2) == 22.22
    # verbatim_internal: only sentence 0 (internal_corpus) = 10/36
    assert report.verbatim_internal_pct == round(100 * 10 / 36, 2) == 27.78
    # union: sentences 0+1 = 18/36 = 50.0 (sentence 2 is paraphrase only, sentence 3 has no match)
    assert report.union_verbatim_pct == 50.0
    # sentence_exact: only sentence 1's score (0.99) is >= 0.95 -> 1 of 4 sentences
    assert report.sentence_exact_pct == 25.0
    # estimate: max(oab, internal) = 27.78, never exceeding the union (50.0, so no capping triggered)
    assert report.estimate_pct == 27.78
    assert report.matched_spans[0].sentence_index == 0 and report.matched_spans[1].sentence_index == 1
    assert len(report.paraphrase_spans) == 1 and report.paraphrase_spans[0].sentence_index == 2
    # 3 sources contributed (internal_corpus, openalex, crossref); core did not -> medium band
    assert report.confidence_band == "medium"
    assert "3 of 4" in report.coverage_statement


def test_confidence_band_changes_when_sources_are_disabled():
    sentences = _sentences()
    matches = [SpanMatch(sentence_index=0, start=0, end=10, token_count=10, snippet="s0",
                         source_id="internal_corpus", signal="verbatim", score=0.9)]

    all_four = [
        SourceCoverage("internal_corpus", True, 1, 1), SourceCoverage("openalex", True, 3, 0),
        SourceCoverage("crossref", True, 3, 0), SourceCoverage("core", True, 3, 0),
    ]
    assert compute_report(sentences, matches, all_four).confidence_band == "low"     # only 1 source ever matched

    only_internal = [SourceCoverage("internal_corpus", True, 1, 1)]
    assert compute_report(sentences, matches, only_internal).confidence_band == "low"

    no_matches = [SourceCoverage("internal_corpus", True, 1, 0)]
    assert compute_report(sentences, [], no_matches).confidence_band == "very_low"

    # 4 independently-matching sources -> 'high'
    matches4 = [
        SpanMatch(0, 0, 10, 10, "s0", "internal_corpus", "verbatim", 0.9),
        SpanMatch(1, 10, 18, 8, "s1", "openalex", "verbatim", 0.8),
        SpanMatch(2, 18, 30, 12, "s2", "crossref", "verbatim", 0.75),
        SpanMatch(3, 30, 36, 6, "s3", "core", "verbatim", 0.72),
    ]
    cov4 = [SourceCoverage(s, True, 1, 1) for s in ("internal_corpus", "openalex", "crossref", "core")]
    assert compute_report(sentences, matches4, cov4).confidence_band == "high"


def test_estimate_never_exceeds_union_and_never_counts_paraphrase_as_verbatim():
    sentences = _sentences()
    # only a paraphrase match anywhere - estimate/union must both stay at 0, paraphrase reported separately
    matches = [SpanMatch(2, 18, 30, 12, "s2", "crossref", "paraphrase", 0.3)]
    report = compute_report(sentences, matches, [SourceCoverage("crossref", True, 1, 1)])
    assert report.estimate_pct == 0.0 and report.union_verbatim_pct == 0.0
    assert report.verbatim_oab_pct == 0.0 and report.verbatim_internal_pct == 0.0
    assert len(report.paraphrase_spans) == 1 and report.matched_spans == []


def test_copyleaks_disclaimer_only_appears_when_a_copyleaks_result_is_attached():
    sentences = _sentences()
    without = compute_report(sentences, [], [])
    assert not any("Copyleaks" in d for d in without.disclaimers)
    withit = compute_report(sentences, [], [], copyleaks={"score": 12.5, "provider": "copyleaks"})
    assert any("Copyleaks" in d for d in withit.disclaimers)
    assert withit.copyleaks == {"score": 12.5, "provider": "copyleaks"}
