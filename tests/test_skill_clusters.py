"""T40 (packet 023): sanity checks on the FROZEN PM-authored cluster table itself (never on its own content
- the table's members are exactly PM's own list, never added/removed/reworded here) and on
`cluster_siblings()`, the one function matching.py's own cluster expansion calls."""
from ajp.applicants.profile_extract import GENERIC_SKILL_TERM_STOPLIST
from ajp.applicants.skill_clusters import FROZEN_CLUSTERS, cluster_siblings


def test_the_frozen_table_has_exactly_the_8_pm_named_clusters():
    assert set(FROZEN_CLUSTERS) == {
        "C1_ML_AI_CORE", "C2_NLP", "C3_DATA_ANALYTICS", "C4_CAD_DESIGN_TOOLS", "C5_CAE_FEM_SIMULATION",
        "C6_FLUIDS", "C7_MATERIALS_LAB", "C8_MECHANICAL_UMBRELLA",
    }


def test_no_member_is_shared_across_two_different_clusters():
    """A future edit to this frozen table that accidentally introduced a cross-cluster collision would
    silently change which root's label wins the tilde-reason tie-break - caught here explicitly."""
    seen = {}
    for cluster_id, members in FROZEN_CLUSTERS.items():
        for member in members:
            key = member.lower()
            assert key not in seen, f"{member!r} is in both {seen.get(key)} and {cluster_id}"
            seen[key] = cluster_id


def test_no_cluster_member_is_a_stoplisted_bare_generic():
    """T40's own explicit instruction: the stoplist wins even over a cluster member - this is a standing
    invariant of the frozen table itself today (none of its real members collide), re-checked here so a
    future table edit that DID introduce a collision fails loudly rather than being silently filtered at
    match time with no one noticing why a cluster stopped working."""
    for members in FROZEN_CLUSTERS.values():
        for member in members:
            assert member.lower() not in GENERIC_SKILL_TERM_STOPLIST


def test_explicitly_excluded_terms_are_really_absent_from_every_cluster():
    """PM's own named exclusions (packet 023) - a regression against ever silently re-adding one of these,
    each for the exact real reason PM gave (see skill_clusters.py's own module comment)."""
    all_members_lower = {m.lower() for members in FROZEN_CLUSTERS.values() for m in members}
    excluded = {
        "ai", "cv", "robot", "robotics", "manipulation", "statistics",
        "electrical engineering", "chemical engineering", "civil engineering", "structural engineering",
        "automation technology", "mechatronics", "mechatronic",
        "aerospace engineering", "industrial engineering",
    }
    assert all_members_lower.isdisjoint(excluded)


def test_cluster_siblings_returns_every_other_member_never_the_root_itself():
    siblings = cluster_siblings("machine learning")
    assert "machine learning" not in siblings
    assert "robot learning" in siblings
    assert "artificial intelligence" in siblings
    assert len(siblings) == 5    # the other 5 of C1's own 6 members


def test_cluster_siblings_is_case_insensitive_on_the_root():
    assert cluster_siblings("MACHINE LEARNING") == cluster_siblings("machine learning")
    assert cluster_siblings("cad") == cluster_siblings("CAD")


def test_cluster_siblings_of_a_non_member_term_is_empty():
    assert cluster_siblings("python") == []
    assert cluster_siblings("engineering") == []
    assert cluster_siblings("electrical engineering") == []


def test_cluster_siblings_preserves_the_tables_own_canonical_casing():
    siblings = cluster_siblings("cad")
    assert "SolidWorks" in siblings and "solidworks" not in siblings
    assert "AutoCAD" in siblings
