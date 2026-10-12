"""The human review queue for the classifier's own output (T48, packet 028).

WHY: ``kind`` decides whether a listing is visible at all - 'other' is hidden by default. Until now that
decision was write-only: a row was classified, and nothing ever asked whether the classifier was RIGHT.
The two PM audits on record both found real ads wrongly buried in 'other' (T33: research-support staff
titles; T35/T36: two titles PM had to rule on by hand), and both were found by manual DB inspection, not
by any signal the platform produced. This queue is that signal: it surfaces the classifications the
classifier itself is least sure about, with the excerpt that produced them, so the next audit starts from
a worklist instead of from a fresh guess at what to look at.

DESIGN RULES, inherited from this project's own precedents:
  * A human decision is RECORDED, never auto-applied (PM's standing ruling for the OpenAlex review queue:
    name-only matches are reviewed, never auto-merged). ``listing`` keeps the machine's answer; ``human_kind``
    here is the correction, and applying it is its own deliberate step with its own evidence.
  * 'other' is never queued just for being 'other'. Most unclassified rows are correctly unclassified, and
    queueing all of them would bury the handful that matter. Only the two bounded, named cases below are.
  * Nothing is fabricated. A row with no evidence is queued as "we found nothing", never as a guess.

WHAT GETS QUEUED (three reasons, and only these three):
  low_confidence        - a rule fired, but a weak one (conf < LOW_CONFIDENCE_THRESHOLD): a broad stem like
                          a bare 'projekat' or an ERC mention that survived its own funding-source guard.
  excluded_by_rule      - kind='other' because a rule positively identified an EXCLUSION (a non-academic
                          role). Bounded and reviewable - the classifier made a real decision there.
  unclassified_academic - kind='other' with NO rule at all, but on an academic-looking row (a known academic
                          employer, or an academic position noun in the title). This is the T33/T35/T36
                          failure mode exactly: a real ad the vocabulary simply does not cover yet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ajp.extract.evidence import CONFIDENCE_MODEL

# The cut below which a fired rule is treated as "worth a human look". Chosen against the tiers actually
# in use (ajp.extract.evidence / rs_text._KIND_TIERS / intl_text._POSITION_TIERS_INTL): 0.70 separates the
# specific, phrase-shaped rules (0.80-0.95) from the two deliberately-distrusted ones - a bare research
# noun (0.60), a bare grant-ish stem (0.60) and the ambiguous ERC branch (0.55). It is a threshold on a
# rule-strength PRIOR, not a probability cut (see that module's docstring).
LOW_CONFIDENCE_THRESHOLD = 0.70

REASONS = ("low_confidence", "excluded_by_rule", "unclassified_academic")
STATUSES = ("pending", "confirmed", "corrected", "dismissed")


def doubt_reason(*, kind: str, conf: Optional[float], rule: Optional[str],
                 academic_context: bool = False) -> Optional[str]:
    """Why this classification should be looked at by a human, or None if it should not.

    Pure - no DB, no I/O - so the rule for "what is worth reviewing" is unit-testable on its own and can
    be argued about without a database. ``academic_context`` is the caller's already-computed judgement
    that the row is plausibly academic (a known academic employer, an academic position noun in the
    title): it is what keeps 'unclassified_academic' bounded instead of queueing every unmatched row."""
    if kind == "other":
        if rule:
            return "excluded_by_rule"
        # No rule fired at all. Only worth a human if there is a reason to think a rule SHOULD have fired -
        # otherwise this is just a correctly-unclassified row and queueing it would bury the real ones.
        return "unclassified_academic" if academic_context else None
    if conf is not None and conf < LOW_CONFIDENCE_THRESHOLD:
        return "low_confidence"
    return None


# --------------------------------------------------------------------------- queueing --------------------
def queue_classification(conn: psycopg.Connection, listing_id: Any, field: str, *, kind: str,
                         conf: Optional[float], raw: Optional[str], rule: Optional[str],
                         zone: Optional[str] = None, guards: Sequence[str] = (),
                         academic_context: bool = False) -> Optional[str]:
    """Queue one classification for review if it is doubted. Returns the reason, or None if not queued.

    Idempotent: re-queueing the same (listing, field) while a review is still pending REFRESHES the
    existing row (the classifier's answer may have changed with the rules) rather than piling up
    duplicates that would each have to be dismissed by hand. A row already decided by a human is never
    re-opened - a review closed stays closed.
    """
    reason = doubt_reason(kind=kind, conf=conf, rule=rule, academic_context=academic_context)
    if reason is None:
        return None
    # The WHERE NOT EXISTS is what makes a human's decision STICK. Without it, the partial unique index
    # only covers PENDING rows: once a review is confirmed/corrected/dismissed it leaves that index, so a
    # later re-queue would insert a fresh pending row and quietly undo the human's answer - and a re-run
    # of the backfill would resurrect everything a reviewer had already dismissed, forever.
    conn.execute(
        """INSERT INTO classification_review
               (listing_id, field, classified_as, conf, raw, rule, zone, guards, model, reason)
           SELECT %(lid)s, %(field)s, %(kind)s, %(conf)s, %(raw)s, %(rule)s, %(zone)s, %(guards)s,
                  %(model)s, %(reason)s
            WHERE NOT EXISTS (SELECT 1 FROM classification_review d
                               WHERE d.listing_id = %(lid)s AND d.field = %(field)s
                                 AND d.status <> 'pending')
           ON CONFLICT (listing_id, field) WHERE status = 'pending' DO UPDATE SET
               classified_as = EXCLUDED.classified_as, conf = EXCLUDED.conf, raw = EXCLUDED.raw,
               rule = EXCLUDED.rule, zone = EXCLUDED.zone, guards = EXCLUDED.guards,
               model = EXCLUDED.model, reason = EXCLUDED.reason, created_at = now()""",
        {"lid": listing_id, "field": field, "kind": kind, "conf": conf, "raw": raw, "rule": rule,
         "zone": zone, "guards": Jsonb(list(guards)), "model": CONFIDENCE_MODEL, "reason": reason})
    return reason


# --------------------------------------------------------------------------- reading ---------------------
_REVIEW_SELECT = """
    r.id, r.listing_id, r.field, r.classified_as, r.conf, r.raw, r.rule, r.zone, r.guards, r.model,
    r.reason, r.status, r.human_kind, r.reviewer, r.reviewed_at, r.notes, r.created_at,
    l.title_raw AS listing_title, l.url AS listing_url, l.org_raw AS listing_org, l.kind AS listing_kind,
    l.position_kind AS listing_position_kind
"""


def fetch_review_queue(conn: psycopg.Connection, *, status: str = "pending", reason: Optional[str] = None,
                       limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Open reviews, weakest-evidence-relevant first: 'unclassified_academic' (a real ad we failed to
    classify at all, the T33 failure mode) ahead of 'excluded_by_rule' ahead of 'low_confidence' (a rule
    did fire, it is just a broad one), then by confidence ascending, then oldest first."""
    where, params = "r.status = %(status)s", {"status": status, "limit": limit, "offset": offset}
    if reason:
        where += " AND r.reason = %(reason)s"
        params["reason"] = reason
    # dict_row on the CURSOR, not on the connection: this module must work over whatever connection the
    # caller already holds (the API's own dict_row one, a plain tuple-row one from a script or a test),
    # without silently depending on how it was configured.
    rows = conn.cursor(row_factory=dict_row).execute(
        f"""SELECT {_REVIEW_SELECT}
              FROM classification_review r JOIN listing l ON l.id = r.listing_id
             WHERE {where}
             ORDER BY CASE r.reason WHEN 'unclassified_academic' THEN 0
                                    WHEN 'excluded_by_rule' THEN 1
                                    ELSE 2 END,
                      r.conf ASC NULLS FIRST, r.created_at ASC
             LIMIT %(limit)s OFFSET %(offset)s""", params).fetchall()
    return [_row_out(r) for r in rows]


def review_summary(conn: psycopg.Connection) -> Dict[str, Any]:
    """Counts by status and, for the pending ones, by reason - the number the worklist is judged by."""
    # dict_row explicitly: the API's own connection already uses it, and unpacking `for a, b, c in rows`
    # over a dict_row cursor yields the KEYS, not the values (caught live on the first endpoint call).
    rows = conn.cursor(row_factory=dict_row).execute(
        """SELECT status, reason, count(*) AS n FROM classification_review
           GROUP BY status, reason""").fetchall()
    out: Dict[str, Any] = {"total": 0, "by_status": {}, "pending_by_reason": {r: 0 for r in REASONS},
                           "threshold": LOW_CONFIDENCE_THRESHOLD, "model": CONFIDENCE_MODEL}
    for r in rows:
        n = r["n"]
        out["by_status"][r["status"]] = out["by_status"].get(r["status"], 0) + n
        out["total"] += n
        if r["status"] == "pending" and r["reason"] in out["pending_by_reason"]:
            out["pending_by_reason"][r["reason"]] += n
    return out


def _row_out(r) -> Dict[str, Any]:
    d = dict(r)
    for k in ("id", "listing_id"):
        d[k] = str(d[k]) if d.get(k) is not None else None
    for k in ("reviewed_at", "created_at"):
        d[k] = d[k].isoformat() if d.get(k) is not None else None
    return d


# --------------------------------------------------------------------------- deciding --------------------
class ReviewDecisionRefused(ValueError):
    pass


def record_decision(conn: psycopg.Connection, review_id: Any, status: str, *, human_kind: Optional[str] = None,
                    reviewer: Optional[str] = None, notes: Optional[str] = None) -> Dict[str, Any]:
    """Close one review. ``status`` is confirmed / corrected / dismissed:
      confirmed - the machine was right; nothing changes.
      corrected - the machine was wrong; ``human_kind`` is REQUIRED (the DB enforces it too). The value is
                  recorded, NOT written back to ``listing`` - applying it is a separate, deliberate step
                  with its own audit trail, per the standing "a human decision is recorded, never
                  auto-applied" ruling.
      dismissed - not worth reviewing (and stays closed: a closed review is never re-opened by a re-queue).
    """
    if status not in ("confirmed", "corrected", "dismissed"):
        raise ReviewDecisionRefused(f"status must be one of confirmed/corrected/dismissed, got {status!r}")
    if status == "corrected" and not (human_kind or "").strip():
        raise ReviewDecisionRefused("a 'corrected' review must name the correct kind in human_kind")
    row = conn.cursor(row_factory=dict_row).execute(
        """UPDATE classification_review
              SET status = %s, human_kind = %s, reviewer = %s, notes = %s, reviewed_at = %s
            WHERE id = %s RETURNING id, listing_id, field, human_kind, status""",
        (status, human_kind, reviewer, notes, datetime.now(timezone.utc), review_id)).fetchone()
    if row is None:
        raise ReviewDecisionRefused(f"no review with id {review_id!r}")
    return {"id": str(row["id"]), "listing_id": str(row["listing_id"]), "field": row["field"],
            "status": row["status"], "human_kind": row["human_kind"], "applied": False}
