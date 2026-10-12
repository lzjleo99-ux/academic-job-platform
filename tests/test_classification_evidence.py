"""T48 (packet 028): the classifier's evidence - confidence + raw excerpt for `kind` and `position_kind`,
the only two extracted values on `listing` that had neither (D6).

The first block pins the REFACTOR: the public classifiers must return exactly what they returned before
the evidence layer was added. That is the whole risk of this change - 16 connectors depend on those two
functions and their discard gates - so the corpus below is real ad text from this project's own fixtures
and comments, not invented strings.
"""
from __future__ import annotations

import pytest

from ajp.extract import rs_text
from ajp.extract.evidence import CONFIDENCE_MODEL, Classification, with_kind_override
from ajp.extract.intl_text import (classify_kind_intl, classify_kind_intl_with_evidence,
                                   kind_row_fields, position_kind_intl,
                                   position_kind_intl_with_evidence)
from ajp.extract.rs_text import classify_kind, classify_kind_with_evidence, kind_row_fields as rs_fields

# Real shapes: live titles/examples recorded in this repo's own comments and fixtures.
RS_CORPUS = [
    "Konkurs za izbor u zvanje docenta",
    "Konkurs za upis studenata u prvu godinu doktorskih studija",
    "Javni poziv za dodelu stipendija",
    "Oglas za praksu studenata",
    "Cenovnik troskova izbora u zvanja",
    "Referat komisije za izbor u zvanje",
    "Noc istrazivaca na Filozofskom fakultetu",
    "Istrazivaci naseg instituta ucesnici seminara",
    "Otvoren poziv za ucesce u projektu SPARK",
    "Konkurs za radno mesto nastavnika",
    "Prijemni ispit za master studije",
    "Praksa - spisak zakljucenih ugovora",
    "",
]
INTL_CORPUS = [
    ("PhD Fellowship in Computational Pharmacology", "Principal supervisor: Associate Professor X"),
    ("Postdoc in complex fluid flows", "supported by an ERC-funded research program"),
    ("ERC Starting Grant: open call for host institutions", ""),
    ("Catering Assistant", "University of X"),
    ("Research Technician", ""),
    ("Workday Solution Expert", ""),
    ("Kindergarten Team Lead", ""),
    ("Faculty Position in Finance and Accounting", "Assistant/Associate/Full Professor"),
    ("Post-Doctorant F/H en informatique", ""),
    ("Doktorand i maskininlärning", ""),
    ("Summer internship in the lab", ""),
    ("Open day for prospective students", ""),
    ("", ""),
]


def test_public_classifiers_are_unchanged_by_the_evidence_layer():
    """The refactor must be behaviour-preserving: every connector's kind/position_kind and, above all,
    its `if kind == 'other'` discard gate depend on these returning exactly what they returned before."""
    for text in RS_CORPUS:
        assert classify_kind(text) == classify_kind_with_evidence(text).kind
        assert rs_text.position_kind(text) == (lambda ev, rs: (ev.kind, rs))(
            *rs_text.position_kind_with_evidence(text))
    for title, body in INTL_CORPUS:
        assert classify_kind_intl(title, body) == classify_kind_intl_with_evidence(title, body).kind
        assert position_kind_intl(title, body) == position_kind_intl_with_evidence(title, body).kind


def test_other_is_never_confidence_zero():
    """'other' means NO positive evidence, which is not the same as certainty that it is not a job.
    Modelled as None (NULL), never 0.0 - 0.0 would be a confident negative the classifier never made."""
    pk, _ = rs_text.position_kind_with_evidence("Potpuno nepovezan naslov")
    assert pk.kind == "other"
    assert pk.conf is None, "conf must be None for 'other', never 0.0"
    k = classify_kind_intl_with_evidence("Completely unrelated title", "")
    assert k.kind == "other" and k.conf is None


def test_an_exclusion_still_carries_its_evidence_even_though_conf_is_null():
    """kind='other' reached by positively identifying a NON-academic role is a real decision with real
    evidence - auditable and reviewable - so raw/rule are populated while conf stays None."""
    k = classify_kind_intl_with_evidence("Catering Assistant", "University of X")
    assert k.kind == "other" and k.conf is None
    assert k.rule == "kind.non_academic_role"
    assert k.raw, "an exclusion must still record the span that excluded it"


def test_d6_a_confidence_never_exists_without_its_excerpt():
    """Every value carries the raw text it came from. The DB enforces this too (0026), but the Python
    side must never even be able to produce a pair that would violate it."""
    for text in RS_CORPUS:
        k = classify_kind_with_evidence(text)
        if k.conf is not None:
            assert k.raw and k.raw.strip()
    for title, body in INTL_CORPUS:
        for ev in (classify_kind_intl_with_evidence(title, body), position_kind_intl_with_evidence(title, body)):
            if ev.conf is not None:
                assert ev.raw and ev.raw.strip()


def test_a_guard_firing_weakens_the_surviving_decision():
    """Counter-evidence is real signal. Here the performer-audition guard suppresses the grant_call rule
    ('javni poziv' + 'solisticki nastup', pr.ac.rs's live case) and the employment rule further down still
    fires on 'radno mesto' - but it fires on text a negative guard has already flagged, so it must score
    BELOW the same rule reaching the same verdict on clean text.

    (The narrative-past-tense guard cannot demo this: it gates employment AND grant_call alike, so text
    tripping it correctly ends at 'other' rather than at a weakened survivor.)"""
    clean = classify_kind_with_evidence("Oglas za popunjavanje radnog mesta")
    guarded = classify_kind_with_evidence("Javni poziv za solisticki nastup i konkurs za radno mesto")
    assert clean.kind == "employment" and guarded.kind == "employment"
    assert "performance_audition" in guarded.guards, "the suppressed grant_call rule must be bookkept"
    assert guarded.conf < clean.conf, "a decision reached after a guard fired is weaker, never equal"
    # ...and the guard's whole purpose: news NARRATING a project is not a call to action at all.
    narrative = classify_kind_with_evidence("Istrazivaci su predstavili projekat na konferenciji")
    assert narrative.kind == "other"
    # 'dodela' is tense-neutral in Serbian and deliberately NOT a guard (rs_text's own comment records the
    # live counter-example both ways), so a real scholarship call must still be found:
    assert classify_kind_with_evidence("Konkurs za dodelu stipendija").kind == "grant_call"


def test_position_kind_discounts_a_body_only_match_kind_does_not():
    """The zone discount applies to ``position_kind`` but NOT to ``kind``, and the split is deliberate:

    position_kind_intl has a live counter-example for body text actively misleading it - KU DK's real PhD
    ad whose body named the supervisor's own rank, which outranked the advertised PhD role (that is why
    'phd' gets title-level precedence there at all). So the same match found only in the body scores lower.

    classify_kind_intl is the opposite BY DESIGN: every one of its branches scans title + description as
    one text on purpose, because a real ad as vague as 'Faculty Position in Finance and Accounting' names
    its actual rank only in the body. Discounting that would penalise the exact behaviour the rule exists
    for - measured on the live DB it put 977 of 1,296 queued rows in a single bucket purely for matching
    in the body, which is noise, not doubt. The zone is still RECORDED: a reviewer should know whether the
    title or the body produced the verdict."""
    in_title = position_kind_intl_with_evidence("Research Fellow in Particle Physics", "")
    in_body = position_kind_intl_with_evidence("Vacancy at CERN", "the role is a Research Fellow position")
    assert in_title.zone == "title" and in_body.zone == "body"
    assert in_title.conf > in_body.conf, "position_kind: a body-only match is weaker"

    k_title = classify_kind_intl_with_evidence("Research Fellow in Particle Physics", "")
    # the real jobs.ac.uk shape: a title that names no rank at all, with the rank only in the body
    k_body = classify_kind_intl_with_evidence("Vacancy in Finance and Accounting",
                                              "appointable at Assistant Professor level")
    assert k_title.zone == "title" and k_body.zone == "body"
    assert k_title.conf == k_body.conf, "kind: recorded, but NOT discounted - combined text is the design"


def test_row_fields_always_ship_the_kind_together_with_its_evidence():
    """One call, so a connector cannot set a kind and forget the excerpt - the two never diverge."""
    f = kind_row_fields("PhD Fellowship in Biophysics", "we seek a candidate")
    assert set(f) == {"kind", "kind_conf", "kind_raw", "position_kind", "position_kind_conf",
                      "position_kind_raw"}
    if f["kind"] != "other":
        assert f["kind_raw"], "a classified row must carry the span it was classified from"
    g = rs_fields("Konkurs za izbor u zvanje docenta", "Konkurs za izbor u zvanje docenta")
    assert g["kind"] == "employment" and g["kind_raw"] == "izbor u zvanje"
    assert g["position_kind"] == "professor" and g["position_kind_raw"] == "docenta"
    assert g["rs_research_title"] is None


def test_an_overridden_kind_loses_the_classifiers_evidence():
    """A connector that overrides the classifier (a configured default_kind, a board category, an
    academic_ad gate) must not keep the classifier's excerpt: it describes a decision that was NOT the
    one persisted, and leaving it there would be fabricated provenance (D6)."""
    f = {"kind": "internship", "kind_conf": 0.80, "kind_raw": "praksa",
         "position_kind": "intern", "position_kind_conf": 0.80, "position_kind_raw": "praksa"}
    assert with_kind_override(f, "internship") == f, "same kind: a no-op, evidence intact"
    out = with_kind_override(f, "employment")
    assert out["kind"] == "employment"
    assert out["kind_conf"] is None and out["kind_raw"] is None
    assert out["position_kind_conf"] == 0.80, "only the OVERRIDDEN field's evidence is dropped"


def test_confidence_model_is_versioned():
    """A persisted score must be traceable to the model that produced it, so an old number is never read
    as if it came from today's tiers."""
    assert CONFIDENCE_MODEL
    assert Classification(kind="employment", conf=0.85, raw="radno mesto",
                          rule="kind.employment").as_dict()["model"] == CONFIDENCE_MODEL


def test_conf_bounds_are_clamped():
    """The DB CHECKs conf BETWEEN 0 AND 1; the Python side must never be able to emit a value outside it
    no matter how the tier x zone x guard factors combine."""
    from ajp.extract.evidence import score
    assert 0.0 <= score(0.60, zone="body", guards_fired=3) <= 1.0
    assert score(0.60, zone="body") < score(0.60, zone="title")
    assert score(0.60, guards_fired=1) < score(0.60, guards_fired=0)
