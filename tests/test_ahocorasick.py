"""T41 (packet 024): basic correctness tests for the vendored Aho-Corasick automaton itself, independent
of any project-specific (zone/weight/stoplist) logic - that integration is tested separately in
test_applicant_matching.py's own identity-diff regression against the old regex engine."""
from ajp.applicants.ahocorasick import AhoCorasick


def _matches(patterns, text):
    ac = AhoCorasick(patterns)
    return sorted(ac.iter_matches_lowercase(text.lower()))


def test_a_single_pattern_matches_every_occurrence():
    assert _matches(["cat"], "the cat sat on the cat mat") == [(4, 7, "cat"), (19, 22, "cat")]


def test_disjoint_patterns_all_found():
    result = _matches(["cat", "dog"], "a cat and a dog")
    assert (2, 5, "cat") in result and (12, 15, "dog") in result


def test_overlapping_patterns_both_reported_the_caller_resolves_overlap():
    """The automaton itself reports EVERY match, overlapping or not - matching.py's own caller code is
    what picks a non-overlapping, leftmost-longest subset (this is deliberate - see ahocorasick.py's own
    module docstring). The classic Aho-Corasick textbook example (patterns he/she/hers/his over "ushers") -
    verified by actually running it, not hand-computed: "ushers" is u-s-h-e-r-s (indices 0-5), so "she" is
    at [1:4), "he" at [2:4), "hers" at [2:6)."""
    result = _matches(["he", "hers", "she", "his"], "ushers")
    found = {(s, e, p) for s, e, p in result}
    assert found == {(1, 4, "she"), (2, 4, "he"), (2, 6, "hers")}


def test_a_pattern_not_present_is_never_matched():
    assert _matches(["xyz"], "abcdef") == []


def test_empty_pattern_list_matches_nothing():
    assert _matches([], "anything at all") == []


def test_case_is_irrelevant_once_both_sides_are_lowercased_by_the_caller():
    assert _matches(["cad"], "CAD required") == [(0, 3, "cad")]


def test_multi_word_pattern_with_internal_punctuation_matches_literally():
    assert _matches(["computer-aided design"], "a computer-aided design role") == [(2, 23, "computer-aided design")]


def test_a_pattern_that_is_a_prefix_of_another_both_reported():
    result = _matches(["fluid", "fluid mechanics"], "fluid mechanics research")
    found = {(s, e, p) for s, e, p in result}
    assert (0, 5, "fluid") in found
    assert (0, 15, "fluid mechanics") in found
