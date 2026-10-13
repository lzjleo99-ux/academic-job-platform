"""GET /v1/classification-review - the classifier's own review worklist (T48, packet 028).

Reading is public, like every other read route in this package: it surfaces what the classifier is unsure
about, with the excerpt the decision was made on, so a human can work through it.

WRITING needs a secret, and now has one (T49, 2026-10-13): POST .../{id}/decision records the human's
answer and is gated on AJP_ADMIN_TOKEN (ajp.api.deps.require_admin, refuse-when-unconfigured). This is an
OPERATOR action, not public self-service - the public self-service endpoints are bounded by
ajp.api.ratelimit instead, because a browser cannot hold a secret.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from psycopg import Connection

from ajp.api.deps import get_conn, require_admin
from ajp.api.schemas import ClassificationReview, ClassificationReviewResponse, ReviewDecisionOut
from ajp.classification_review import REASONS, STATUSES, ReviewDecisionRefused, fetch_review_queue, \
    record_decision, review_summary

router = APIRouter(prefix="/v1", tags=["classification-review"])


@router.get("/classification-review", response_model=ClassificationReviewResponse)
def classification_review(status: str = "pending", reason: Optional[str] = None,
                          limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                          conn: Connection = Depends(get_conn)) -> ClassificationReviewResponse:
    """Open review items, worst-first: 'unclassified_academic' (a real ad the vocabulary does not cover
    yet - the failure mode PM's own T33/T35/T36 audits found by hand) before 'excluded_by_rule' (the
    classifier positively identified an exclusion) before 'low_confidence' (a rule fired, but a broad one).
    """
    if reason is not None and reason not in REASONS:
        reason = None      # never 500 on an unknown filter value; an empty result says it plainly enough
    summary = review_summary(conn)
    items = fetch_review_queue(conn, status=status, reason=reason, limit=limit, offset=offset)
    return ClassificationReviewResponse(
        items=[ClassificationReview(**i) for i in items],
        total=summary["total"], by_status=summary["by_status"],
        pending_by_reason=summary["pending_by_reason"],
        threshold=summary["threshold"], model=summary["model"])


class ReviewDecisionIn(BaseModel):
    """A human's answer to one queued classification. 'corrected' REQUIRES human_kind - the DB enforces
    the same rule, but failing at the schema layer returns a 422 that says so before the DB is touched."""
    status: str
    human_kind: Optional[str] = None
    reviewer: Optional[str] = None
    notes: Optional[str] = None


@router.post("/classification-review/{review_id}/decision", response_model=ReviewDecisionOut)
def decide_classification_review(review_id: str, body: ReviewDecisionIn, request: Request,
                                 conn: Connection = Depends(get_conn),
                                 _admin: None = Depends(require_admin)):
    """Record a human decision on one review. Never auto-applies: `listing` keeps the machine's answer;
    `human_kind` is the correction, recorded for a deliberate separate step (the standing 'a human
    decision is recorded, never auto-applied' ruling)."""
    try:
        return record_decision(conn, review_id, body.status, human_kind=body.human_kind,
                               reviewer=body.reviewer, notes=body.notes)
    except ReviewDecisionRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
