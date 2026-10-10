"""T40 (packet 023, PM ruling OPTION_B_APPROVED_WITH_PM_FROZEN_CLUSTER_TABLE - resolving T37's own
need_input): curated, static, symmetric skill-equivalence clusters, used ONLY by
ajp.applicants.matching's own skill_relevance factor to expand the APPLICANT side of its phrase match.

This table is FROZEN exactly as PM specified it - no member added, removed, or reworded. PM's own explicit
rationale for each exclusion (see the module docstring's own "deliberately excluded" list at the bottom) is
preserved verbatim in comments, since a future edit must re-justify any change against those same reasons,
never just re-add a plausible-looking term.

The mechanism this table backs (see matching.py's own `_cluster_expand` and its callers) is scoped
deliberately narrowly: it NEVER touches the discipline factor, listing-side inference, the persisted
`inferred_*` columns (T39), or any connector - it only ever adds MORE candidate surface phrases to
skill_relevance's own existing search, for an applicant term that happens to be a cluster member.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, List

# T40's own real motivating cases (PM's own live, measured evidence, packet 023):
#   - KTH "Postdoc in Robot Learning for Manipulation": the ML golden CV states "machine learning" but
#     never "robot learning" verbatim - C1 links them so the CV's own real ML/AI expertise is recognised.
#   - "Assistant Professor of Natural Language Processing": the CV states the abbreviation "NLP", the
#     listing spells the full term out - C2 links them.
#   - Both SUSMAT "Computer-Aided Design" postdoc rows: the mechanical CV states "CAD", the listing spells
#     out "Computer-Aided Design" - C4 links them.
# C5-C8 are PM's own proactive extension of the SAME real gap class to the mechanical CV's other stated
# skills/field (FEM/ANSYS, CFD/fluid mechanics, materials testing, "mechanical engineering" itself), so a
# future real-world listing phrased slightly differently from the CV's own exact wording is not missed
# merely because T40's own regression tests only reproduced the 3 defects PM had already measured live.
FROZEN_CLUSTERS: "Dict[str, List[str]]" = {
    "C1_ML_AI_CORE": [
        "machine learning", "deep learning", "reinforcement learning", "robot learning",
        "statistical machine learning", "artificial intelligence",
    ],
    "C2_NLP": [
        "natural language processing", "NLP", "computational linguistics", "language technology",
        "text mining", "information extraction",
    ],
    "C3_DATA_ANALYTICS": [
        "data science", "data scientist", "data analysis", "data analytics", "data mining", "big data",
        "statistical analysis", "statistical modelling", "statistical modeling",
    ],
    "C4_CAD_DESIGN_TOOLS": [
        "CAD", "computer-aided design", "computer aided design", "AutoCAD", "SolidWorks", "CATIA",
        "Siemens NX", "Creo", "Inventor",
    ],
    "C5_CAE_FEM_SIMULATION": [
        "FEM", "FEA", "finite element method", "finite element analysis", "finite element modelling",
        "finite element modeling", "computational mechanics", "ANSYS", "Abaqus", "COMSOL",
    ],
    "C6_FLUIDS": [
        "CFD", "computational fluid dynamics", "fluid mechanics", "fluid dynamics", "aerodynamics",
        "turbomachinery", "hydrodynamics",
    ],
    "C7_MATERIALS_LAB": [
        "materials testing", "material testing", "materials characterization", "material characterization",
        "materials characterisation", "material characterisation", "experimental mechanics",
    ],
    "C8_MECHANICAL_UMBRELLA": [
        "mechanical engineering", "machine design", "mechanical design", "mechanical systems",
        "precision mechanics", "precision mechanic",
    ],
}

# PM's own explicit, reasoned exclusions (packet 023) - a future edit considering re-adding one of these
# must re-justify it against the SAME real risk named here, never just because it looks plausible:
#   - bare "AI": real false positives (French "j'ai" and others) - "artificial intelligence" (the real
#     multi-word phrase, C1) already covers the genuine signal without this risk.
#   - bare "CV": a curriculum-vitae false positive, not a computer-vision signal.
#   - bare "robot"/"robotics"/"manipulation": too broad on their own - "robot learning" itself (already a
#     C1 member) already covers the one real case (KTH) that motivated this cluster at all.
#   - bare "statistics": stays a standalone literal skill only, never cluster-expanded.
#   - electrical/chemical/civil/structural engineering, automation technology, mechatronics/mechatronic:
#     NOT mutually interchangeable with "mechanical engineering" - this is the EXACT T36c cross-pollution
#     lesson (a mechanical engineer's CV must never score an electrical/chemical engineering listing highly
#     just because both share a broad discipline bucket) - re-litigated and re-confirmed here, deliberately,
#     for skill_relevance's own cluster mechanism, not just the discipline factor.
#   - aerospace engineering, industrial engineering: conservative - no current golden-CV evidence either
#     way, left out rather than guessed.
#   - any medical/biology/chemistry generic, and any term already on GENERIC_SKILL_TERM_STOPLIST.

_MEMBER_TO_CLUSTERS: "Dict[str, FrozenSet[str]]" = {}
# member.lower() -> the cluster's OWN canonical spelling of that exact member (used when that member is
# itself the matched surface phrase, so skill_relevance's own weight_by_lower never has to guess a casing).
_CANONICAL_SPELLING: "Dict[str, str]" = {}
for _cluster_id, _members in FROZEN_CLUSTERS.items():
    for _member in _members:
        _key = _member.lower()
        _MEMBER_TO_CLUSTERS.setdefault(_key, set()).add(_cluster_id)
        # Two different clusters never happen to share a member in this frozen table (checked below in
        # test_skill_clusters.py), so the LAST assignment here is also the only one in practice; kept as a
        # plain assignment (not a conditional) so a future table edit introducing an accidental duplicate
        # fails that explicit test loudly, rather than silently picking whichever member happened to be
        # inserted first.
        _CANONICAL_SPELLING[_key] = _member
_MEMBER_TO_CLUSTERS = {k: frozenset(v) for k, v in _MEMBER_TO_CLUSTERS.items()}


def cluster_siblings(root_term: str) -> "List[str]":
    """Every OTHER member (canonical spelling, as written in FROZEN_CLUSTERS) of every cluster `root_term`
    (case-insensitive) belongs to - empty if `root_term` is not a member of any cluster at all. Never
    includes `root_term` itself."""
    root_lower = root_term.strip().lower()
    cluster_ids = _MEMBER_TO_CLUSTERS.get(root_lower)
    if not cluster_ids:
        return []
    out: "List[str]" = []
    seen = {root_lower}
    for cid in cluster_ids:
        for member in FROZEN_CLUSTERS[cid]:
            member_lower = member.lower()
            if member_lower in seen:
                continue
            seen.add(member_lower)
            out.append(member)
    return out
