"""GET /v1/listings, /v1/listings/{id}, /v1/listings/export - packet 007 T14_fastapi."""
from __future__ import annotations

import csv
import io
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
import psycopg.errors
from psycopg import Connection

from ajp.api.deps import get_conn
from ajp.api.filters import ListingFilters, where_sql
from ajp.api.params import listing_filters
from ajp.api.rows import BASE_FROM, BASE_SELECT, row_to_card, row_to_detail
from ajp.api.schemas import ListingDetail, ListingsResponse, Page

router = APIRouter(prefix="/v1", tags=["listings"])

_SORTS = {
    "deadline_asc": "l.deadline ASC NULLS LAST, l.id",
    "posted_desc": "l.posted_at DESC NULLS LAST, l.id",
    "salary_desc": "salary_eur_year_max DESC NULLS LAST, l.id",
}
DEGRADED_NOTES = [
    "discipline filter/facet is a flat EuroSciVoc-code overlap match; hierarchy ('include descendants') "
    "is not implemented - no authoritative EuroSciVoc concept tree has been loaded yet (see /v1/meta)",
]


def _degraded() -> List[str]:
    return list(DEGRADED_NOTES)


@router.get("/listings", response_model=ListingsResponse)
def list_listings(
    f: ListingFilters = Depends(listing_filters),
    sort: str = Query("posted_desc", pattern="^(deadline_asc|posted_desc|salary_desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include: Optional[str] = Query(None, description="comma list; currently accepted: 'org' (nested org object always sent regardless)"),
    conn: Connection = Depends(get_conn),
) -> ListingsResponse:
    where, params = where_sql(f)
    total = conn.execute(f"SELECT count(*) AS n {BASE_FROM} WHERE {where}", params).fetchone()["n"]
    order = _SORTS[sort]
    params = dict(params, limit=page_size, offset=(page - 1) * page_size)
    rows = conn.execute(
        f"SELECT {BASE_SELECT} {BASE_FROM} WHERE {where} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s",
        params,
    ).fetchall()
    total_pages = max(1, (total + page_size - 1) // page_size)
    return ListingsResponse(
        page=Page(page=page, page_size=page_size, total=total, total_pages=total_pages),
        sort=sort, results=[row_to_card(r) for r in rows], degraded=_degraded(),
    )


@router.get("/listings/export")
def export_listings(
    f: ListingFilters = Depends(listing_filters),
    sort: str = Query("posted_desc", pattern="^(deadline_asc|posted_desc|salary_desc)$"),
    format: str = Query("json", pattern="^(csv|json)$"),
    limit: int = Query(1000, ge=1, le=5000, description="hard cap on exported rows"),
    conn: Connection = Depends(get_conn),
):
    where, params = where_sql(f)
    params = dict(params, limit=limit)
    rows = conn.execute(
        f"SELECT {BASE_SELECT} {BASE_FROM} WHERE {where} ORDER BY {_SORTS[sort]} LIMIT %(limit)s", params
    ).fetchall()
    cards = [row_to_card(r) for r in rows]
    if format == "json":
        return [c.model_dump(mode="json") for c in cards]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "title", "org", "country", "city", "kind", "position_kind", "salary_eur_year_min",
               "salary_eur_year_max", "foreigner_eligible", "deadline", "posted_at", "status", "url_sources"])
    for c in cards:
        w.writerow([c.id, c.title_raw, c.org_raw or "", c.country_iso2, c.city or "", c.kind, c.position_kind,
                    c.salary_eur_year.min_eur_year or "", c.salary_eur_year.max_eur_year or "",
                    c.foreigner_eligible, c.deadline or "", c.posted_at or "", c.status,
                    ";".join(s.url for s in c.sources)])
    buf.seek(0)
    return StreamingResponse(buf, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=listings.csv"})


@router.get("/listings/{listing_id}", response_model=ListingDetail)
def get_listing(listing_id: str, conn: Connection = Depends(get_conn)) -> ListingDetail:
    # F14 (2026-10-11): the id column is a uuid, so a malformed path value ('not-a-uuid') made Postgres
    # raise 22P02 and escaped as an unhandled 500 - every mistyped bookmark or crawler hit produced one.
    # An id that cannot be a listing id is simply not found: 404, the same answer as a well-formed id with
    # no row (clients already handle 404 here, and no new status code is introduced).
    try:
        row = conn.execute(f"SELECT {BASE_SELECT} {BASE_FROM} WHERE l.id = %(id)s", {"id": listing_id}).fetchone()
    except psycopg.errors.DataError:
        raise HTTPException(status_code=404, detail="listing not found") from None
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found")
    return row_to_detail(row)
