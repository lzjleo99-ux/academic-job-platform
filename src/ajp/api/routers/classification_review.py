"""GET /v1/classification-review - the classifier's own review worklist (T48, packet 028).

Read-only, like every other route in this package except the thesis/applicants ones: it surfaces what the
classifier is unsure about, with the excerpt the decision was made on, so a human can work through it.

DELIBERATELY NO WRITE ENDPOINT YET. Recording a decision (confirmed/corrected/dismissed) is a mutation,
and this API currently has no authentication at all - /v1/applicants and /v1/thesis already accept writes
from anyone, which is the open item ④ on the roadmap, not a precedent to extend. Adding a second
unauthenticated write route would enlarge an unfixed exposure for no functional gain, so ``record_decision``
ships as a library function (already covered by tests) and the HTTP verb waits for auth. See the PM report:
the two options are a shared-secret header now, or landing it with ④.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from ajp.api.deps import get_conn
from ajp.api.schemas import ClassificationReview, ClassificationReviewResponse
from ajp.classification_review import REASONS, fetch_review_queue, review_summary

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
