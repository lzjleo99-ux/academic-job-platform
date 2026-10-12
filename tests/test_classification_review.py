"""T48 (packet 028): what actually lands in the human review queue, and what must never land there.

The whole value of a review queue is that it is SHORT and RIGHT. A queue that contains every unclassified
row is not a worklist, it is the table again - and the two PM audits on record (T33 research-support
titles, T35/T36 two hand-ruled titles) were both cases of real ads wrongly buried in 'other', i.e. the
failure this must surface and not bury.
"""
from __future__ import annotations

import pytest

from ajp.classification_review import (
    LOW_CONFIDENCE_THRESHOLD,
    ReviewDecisionRefused,
    doubt_reason,
    fetch_review_queue,
    queue_classification,
    record_decision,
    review_summary,
)


def _listing(conn, ext_uid: str = "ad-1", **kw):
    conn.execute("INSERT INTO source (id, kind, base_url) VALUES ('nature_careers','feed','https://x.invalid') "
                 "ON CONFLICT (id) DO NOTHING")
    conn.execute(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, position_kind, kind)
           VALUES (%s,'nature_careers',%s,%s,%s,%s,%s) RETURNING id""",
        (ext_uid, f"https://x.invalid/{ext_uid}", f"https://x.invalid/{ext_uid}",
         kw.get("title_raw", "Some ad"), kw.get("position_kind", "other"), kw.get("kind", "other")))
    return conn.execute("SELECT id FROM listing WHERE ext_uid=%s", (ext_uid,)).fetchone()[0]


# ------------------------------------------------------------------ the pure rule ------------------------
def test_a_confident_classification_is_never_queued():
    assert doubt_reason(kind="employment", conf=0.85, rule="kind.employment") is None


def test_a_weak_rule_that_did_fire_is_queued_as_low_confidence():
    """The two deliberately-distrusted tiers: a bare research noun (0.60) and a bare grant-ish stem (0.60)."""
    assert doubt_reason(kind="grant_call", conf=0.60, rule="kind.grant_call") == "low_confidence"
    assert doubt_reason(kind="employment", conf=0.95, rule="kind.employment") is None
    assert LOW_CONFIDENCE_THRESHOLD == 0.70


def test_a_positive_exclusion_is_queued_because_it_is_a_real_decision():
    """kind='other' reached by identifying a NON-academic role is reviewable - it is the classifier
    actively deciding to hide something, which is exactly what needs a second pair of eyes."""
    assert doubt_reason(kind="other", conf=None, rule="kind.non_academic_role") == "excluded_by_rule"


def test_other_with_no_rule_is_queued_only_when_the_row_looks_academic():
    """The T33/T35/T36 failure mode - a real ad the vocabulary does not cover yet. But an unmatched row
    with no academic signal at all is correctly unclassified, and queueing it would bury the real ones."""
    assert doubt_reason(kind="other", conf=None, rule=None, academic_context=True) == "unclassified_academic"
    assert doubt_reason(kind="other", conf=None, rule=None, academic_context=False) is None


# ------------------------------------------------------------------ the DB round-trip ---------------------
def test_queueing_writes_a_pending_row_carrying_the_evidence(conn):
    lid = _listing(conn, "ad-1")
    reason = queue_classification(conn, lid, "kind", kind="grant_call", conf=0.60, raw="projekat",
                                  rule="kind.grant_call", zone="body", guards=["narrative_past"])
    assert reason == "low_confidence"
    rows = fetch_review_queue(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["listing_id"] == str(lid)
    assert r["classified_as"] == "grant_call" and r["conf"] == 0.60 and r["raw"] == "projekat"
    assert r["rule"] == "kind.grant_call" and r["zone"] == "body"
    assert r["guards"] == ["narrative_past"]
    assert r["model"], "the score must be traceable to the model that produced it"
    assert r["status"] == "pending" and r["reviewed_at"] is None
    assert r["listing_title"] == "Some ad", "a review is useless without the text it is about"


def test_a_not_doubted_classification_is_never_queued(conn):
    lid = _listing(conn, "ad-2")
    assert queue_classification(conn, lid, "kind", kind="employment", conf=0.85, raw="radno mesto",
                                rule="kind.employment") is None
    assert fetch_review_queue(conn) == []


def test_requeueing_refreshes_the_open_row_instead_of_piling_up_duplicates(conn):
    """A re-run (or a changed tier) must update the same question, not create a second one that a human
    then has to dismiss separately - that is how a worklist becomes unmaintainable."""
    lid = _listing(conn, "ad-3")
    queue_classification(conn, lid, "kind", kind="grant_call", conf=0.60, raw="projekat",
                         rule="kind.grant_call")
    # still doubted, just differently - the ambiguous ERC branch rather than the bare-stem one
    queue_classification(conn, lid, "kind", kind="grant_call", conf=0.55, raw="erc",
                         rule="kind.grant_call.erc")
    rows = fetch_review_queue(conn)
    assert len(rows) == 1
    assert rows[0]["classified_as"] == "grant_call" and rows[0]["conf"] == 0.55
    assert rows[0]["raw"] == "erc" and rows[0]["rule"] == "kind.grant_call.erc"
    assert conn.execute("SELECT count(*) FROM classification_review").fetchone()[0] == 1
    # ...and a classification that is NOT doubted never disturbs an open row either way
    assert queue_classification(conn, lid, "kind", kind="employment", conf=0.85, raw="radno mesto",
                                rule="kind.employment") is None
    assert conn.execute("SELECT count(*) FROM classification_review").fetchone()[0] == 1


def test_a_decided_review_is_never_reopened_by_a_requeue(conn):
    lid = _listing(conn, "ad-4")
    queue_classification(conn, lid, "kind", kind="grant_call", conf=0.60, raw="projekat",
                         rule="kind.grant_call")
    rid = fetch_review_queue(conn)[0]["id"]
    record_decision(conn, rid, "dismissed", reviewer="pm")
    queue_classification(conn, lid, "kind", kind="grant_call", conf=0.60, raw="projekat",
                         rule="kind.grant_call")
    assert fetch_review_queue(conn) == [], "a closed review stays closed"
    assert review_summary(conn)["by_status"] == {"dismissed": 1}


def test_ordering_surfaces_the_worst_failure_first(conn):
    """'unclassified_academic' (a real ad we failed to classify AT ALL - the T33 case) must come before a
    merely-broad rule that did fire."""
    for uid, args in (
        ("ad-5", dict(kind="grant_call", conf=0.60, raw="projekat", rule="kind.grant_call")),
        ("ad-6", dict(kind="other", conf=None, raw=None, rule=None, academic_context=True)),
        ("ad-7", dict(kind="other", conf=None, raw="Catering", rule="kind.non_academic_role")),
    ):
        lid = _listing(conn, uid)
        queue_classification(conn, lid, "kind", **args)
    assert [r["reason"] for r in fetch_review_queue(conn)] == [
        "unclassified_academic", "excluded_by_rule", "low_confidence"]


def test_a_correction_is_recorded_but_never_auto_applied(conn):
    """PM's standing ruling for the OpenAlex review queue: a human decision is recorded, never auto-merged.
    `listing` keeps the machine's answer until applying the correction is its own deliberate step."""
    lid = _listing(conn, "ad-8", kind="grant_call")
    queue_classification(conn, lid, "kind", kind="grant_call", conf=0.60, raw="projekat",
                         rule="kind.grant_call")
    rid = fetch_review_queue(conn)[0]["id"]
    out = record_decision(conn, rid, "corrected", human_kind="employment", reviewer="pm", notes="real job")
    assert out["status"] == "corrected" and out["human_kind"] == "employment"
    assert out["applied"] is False, "recording a correction must not silently rewrite the listing"
    assert conn.execute("SELECT kind FROM listing WHERE id=%s", (lid,)).fetchone()[0] == "grant_call"
    reviewed = conn.execute(
        "SELECT status, human_kind, reviewer, reviewed_at FROM classification_review WHERE id=%s",
        (rid,)).fetchone()
    assert reviewed[0] == "corrected" and reviewed[2] == "pm" and reviewed[3] is not None


def test_a_correction_must_name_the_correct_kind(conn):
    lid = _listing(conn, "ad-9")
    queue_classification(conn, lid, "kind", kind="grant_call", conf=0.60, raw="projekat",
                         rule="kind.grant_call")
    rid = fetch_review_queue(conn)[0]["id"]
    with pytest.raises(ReviewDecisionRefused, match="must name the correct kind"):
        record_decision(conn, rid, "corrected")
    with pytest.raises(ReviewDecisionRefused, match="confirmed/corrected/dismissed"):
        record_decision(conn, rid, "whatever")
    with pytest.raises(ReviewDecisionRefused, match="no review with id"):
        record_decision(conn, rid, "confirmed") if False else record_decision(conn, "00000000-0000-0000-0000-000000000000", "confirmed")


def test_the_db_itself_refuses_a_confidence_without_its_excerpt(conn):
    """D6, enforced where it cannot be bypassed: migration 0026's CHECK mirrors the same constraint every
    other {conf, raw} pair on `listing` already has."""
    lid = _listing(conn, "ad-10")
    with pytest.raises(Exception):
        conn.execute("UPDATE listing SET kind_conf = 0.9 WHERE id = %s", (lid,))
        conn.execute("UPDATE listing SET kind_conf = 0.9, kind_raw = NULL WHERE id = %s", (lid,))
    conn.execute("UPDATE listing SET kind_conf = 0.9, kind_raw = 'radno mesto' WHERE id = %s", (lid,))
    row = conn.execute("SELECT kind_conf, kind_raw FROM listing WHERE id=%s", (lid,)).fetchone()
    assert row[0] == 0.9 and row[1] == "radno mesto"
