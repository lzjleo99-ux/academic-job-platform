"""T23: mutation tests - flip one similarity-formula constant, or one remote-classifier regex, and prove an
EXISTING planted test's own assertion then fails. This is not a test of new behaviour: it is proof that
test_similarity_planted_duplication.py and test_jobposting.py's classify_location tests are actually
sensitive to the constants/rules they claim to guard, rather than passing by coincidence regardless of their
value (a real risk with threshold-based heuristics - a planted fixture whose margin is so wide that almost
any threshold in a broad range would pass it is a weak, not-really-a-guard test).

Each test here: (1) captures the ORIGINAL (passing) result, (2) mutates exactly one constant/rule via
monkeypatch (restored automatically at teardown - never a permanent change), (3) reruns the exact same
scenario, (4) asserts the mutated result DIFFERS from the original in the direction the mutation predicts.
Also proves the pipeline itself is deterministic: the same input, run twice with NO mutation, produces a
byte-identical result (same input+cached corpus -> same score, packet 014's own explicit ask).
"""
from __future__ import annotations

from ajp.connectors import jobposting
from ajp.thesis.parser import ThesisDoc
from ajp.thesis.similarity import matcher
from ajp.thesis.similarity.formula import compute_report
from ajp.thesis.similarity.pipeline import run_similarity
from ajp.thesis.similarity.sources.internal_corpus import InternalCorpusAdapter

from tests.test_similarity_planted_duplication import (
    CLEAN_PARAGRAPH,
    INTRO,
    OUTRO,
    PARAPHRASE_PARAGRAPH,
    SOURCE_PARAGRAPH,
    _adapter,
    _doc,
)


# ---------------------------------------------------------------- determinism ------------------------------
def test_the_same_input_and_corpus_produces_a_byte_identical_report_run_twice():
    """packet 014's own explicit ask: same doc + cached corpus -> same score, deterministic, never a
    randomized/time-dependent component sneaking into the formula."""
    doc = _doc(SOURCE_PARAGRAPH)
    r1 = run_similarity(doc, _adapter())
    r2 = run_similarity(doc, _adapter())
    assert r1.estimate_pct == r2.estimate_pct
    assert r1.verbatim_oab_pct == r2.verbatim_oab_pct
    assert [m.score for m in r1.matched_spans] == [m.score for m in r2.matched_spans]
    assert [m.snippet for m in r1.matched_spans] == [m.snippet for m in r2.matched_spans]


# ---------------------------------------------------------------- mutation: similarity formula --------------
def test_raising_the_paraphrase_jaccard_threshold_kills_the_paraphrase_detection_test(monkeypatch):
    """The planted test this mutation targets: test_c_paraphrase_lands_in_the_paraphrase_band_not_verbatim
    asserts len(report_c.paraphrase_spans) > 0. Raising PARAPHRASE_JACCARD_THRESHOLD past the paraphrase
    fixture's own real Jaccard score must make that assertion FAIL - proving the planted test is actually
    sensitive to this constant, not passing regardless of its value. matcher.py binds its own copy via
    `from ... import PARAPHRASE_JACCARD_THRESHOLD` - the module-level name actually consulted at match time
    is matcher.PARAPHRASE_JACCARD_THRESHOLD, not formula's own (a real, easy-to-get-wrong distinction between
    `import module` and `from module import name` - patching the wrong one would silently no-op)."""
    original = run_similarity(_doc(PARAPHRASE_PARAGRAPH), _adapter())
    assert len(original.paraphrase_spans) > 0          # the real, unmutated planted test's own assertion holds

    monkeypatch.setattr(matcher, "PARAPHRASE_JACCARD_THRESHOLD", 0.99)
    mutated = run_similarity(_doc(PARAPHRASE_PARAGRAPH), _adapter())
    assert len(mutated.paraphrase_spans) == 0          # the same planted test's assertion now FAILS


def test_lowering_the_verbatim_containment_threshold_reclassifies_the_paraphrase_as_verbatim(monkeypatch):
    """The planted test this mutation targets: test_c_paraphrase_lands_in_the_paraphrase_band_not_verbatim
    also asserts report_c.matched_spans == [] (no VERBATIM matches at all for a paraphrase). Lowering
    VERBATIM_CONTAINMENT_THRESHOLD far enough must make the paraphrase's own real (non-zero, sub-threshold)
    containment score clear the new, lower bar - proving the boundary between 'verbatim' and 'paraphrase' is
    real, not a threshold so extreme it never binds."""
    original = run_similarity(_doc(PARAPHRASE_PARAGRAPH), _adapter())
    assert original.matched_spans == []                # the real, unmutated planted test's own assertion holds

    monkeypatch.setattr(matcher, "VERBATIM_CONTAINMENT_THRESHOLD", 0.01)
    mutated = run_similarity(_doc(PARAPHRASE_PARAGRAPH), _adapter())
    assert len(mutated.matched_spans) > 0               # the same planted test's assertion now FAILS


def test_raising_the_sentence_exact_threshold_past_1_0_zeroes_out_the_exact_match_count(monkeypatch):
    """SENTENCE_EXACT_THRESHOLD is consulted directly inside formula.compute_report (same module - no
    from-import rebinding trap here, unlike matcher.py's own constants above). No existing planted test
    currently asserts on sentence_exact_pct itself (a real, honest gap this test also fills, standing on its
    own rather than falsely claiming to target an existing assertion): the 3 verbatim-copied sentences clear
    the real 0.95 bar today (sentence_exact_pct > 0); a containment ratio can never exceed 1.0, so raising
    the threshold past it must drop every one of them out, zeroing the count."""
    original = run_similarity(_doc(SOURCE_PARAGRAPH), _adapter())
    assert original.sentence_exact_pct > 0.0            # the real, unmutated 0.95 bar is cleared by real matches

    monkeypatch.setattr("ajp.thesis.similarity.formula.SENTENCE_EXACT_THRESHOLD", 1.5)
    mutated = run_similarity(_doc(SOURCE_PARAGRAPH), _adapter())
    assert mutated.sentence_exact_pct == 0.0            # a genuinely different, mutation-sensitive result


# ---------------------------------------------------------------- mutation: remote-classifier rule ----------
def test_removing_the_fully_remote_pattern_kills_the_remote_classification_test(monkeypatch):
    """The planted test this mutation targets: test_classify_location_is_remote_on_a_real_remote_work_phrase
    (tests/test_jobposting.py) asserts classify_location(...) on 'fully remote' returns location_class=='remote'.
    Removing that one phrase from _REMOTE_SIGNAL's own pattern (keeping every other alternative intact) must
    make the SAME real ad text fall through to 'unknown' - proving the planted test is sensitive to this one
    specific rule, not to the regex object's mere existence."""
    import re

    text = "Software Engineer - fully remote"
    original = jobposting.classify_location(text, has_country=False)
    assert original["location_class"] == "remote"      # the real, unmutated planted test's own assertion holds

    mutated_pattern = re.compile(
        r"\bremote work\b|\bremote position\b|\bwork(?:ing)? from home\b|\bwfh\b|\btelework\w*\b|"
        r"\brad na daljinu\b|\brad od kuće\b", re.I)      # 'fully remote' removed, every other alternative kept
    monkeypatch.setattr(jobposting, "_REMOTE_SIGNAL", mutated_pattern)
    mutated = jobposting.classify_location(text, has_country=False)
    assert mutated["location_class"] == "unknown"       # the same planted test's assertion now FAILS


def test_widening_hybrid_to_match_a_bare_remote_word_breaks_the_precision_test(monkeypatch):
    """The planted test this mutation targets: test_classify_location_never_matches_a_bare_remote_in_an_
    unrelated_sense asserts 'PhD Position in Remote Sensing of Forest Canopies' classifies as 'unknown', not
    'remote' (a bare 'remote' in an unrelated discipline name must never be a work-arrangement signal).
    Widening _REMOTE_SIGNAL to match a bare 'remote' must make that real ad text misclassify - proving the
    planted test is actually sensitive to the pattern staying precise, not vacuously passing."""
    import re

    text = "PhD Position in Remote Sensing of Forest Canopies"
    original = jobposting.classify_location(text, has_country=False)
    assert original["location_class"] == "unknown"     # the real, unmutated planted test's own assertion holds

    monkeypatch.setattr(jobposting, "_REMOTE_SIGNAL", re.compile(r"\bremote\b", re.I))
    mutated = jobposting.classify_location(text, has_country=False)
    assert mutated["location_class"] == "remote"        # the same planted test's assertion now FAILS
