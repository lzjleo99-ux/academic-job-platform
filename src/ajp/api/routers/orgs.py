"""GET /v1/orgs, /v1/orgs/{id} - packet 007 T14_fastapi."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import psycopg.errors
from psycopg import Connection

from ajp.api.deps import get_conn
from ajp.api.schemas import OrgBoardHealthResponse, OrgDetail, OrgSummary
from ajp.registry import fetch_org_board_health

router = APIRouter(prefix="/v1", tags=["orgs"])

_SUMMARY_SELECT = """
    o.id, o.slug, o.name_en, o.name_native, o.country, o.city, o.type, o.homepage,
    (SELECT count(*) FROM listing l WHERE l.org_id = o.id AND l.status = 'open') AS open_listing_count
"""


@router.get("/orgs", response_model=List[OrgSummary])
def list_orgs(country: Optional[List[str]] = Query(None), conn: Connection = Depends(get_conn)) -> List[OrgSummary]:
    where, params = "TRUE", {}
    if country:
        where, params = "o.country = ANY(%(country)s)", {"country": country}
    rows = conn.execute(f"SELECT {_SUMMARY_SELECT} FROM org_master o WHERE {where} ORDER BY o.name_en NULLS LAST", params).fetchall()
    return [OrgSummary(**{**dict(r), "id": str(r["id"])}) for r in rows]


# Declared BEFORE /orgs/{org_id} on purpose: FastAPI matches in registration order, so a literal
# "board-health" segment placed after the path parameter would be swallowed as an org_id and 404.
@router.get("/orgs/board-health", response_model=OrgBoardHealthResponse)
def org_board_health(conn: Connection = Depends(get_conn)) -> OrgBoardHealthResponse:
    """Per-organisation health of the rs_org_boards fan-out source, attributed by org slug.

    Answers PM's own F4 finding (debug_sweep_2026-10-10.md): "rs_org_boards partially degraded - 3
    sub-URLs tripped R1" was reported honestly but unactionably, because /v1/sources aggregates
    crawl_state by source_id and returns reason strings with no attribution. This endpoint reads the
    per-org reports run_ledger.http_counters already persists, so a degraded org is always paired with
    the exact board URL and the reason it was refused.

    Read-only, and it invents nothing: with no fan-out run recorded yet it returns zeroed rollups
    rather than an error, so a fresh install is a valid empty answer rather than a 500.
    """
    return OrgBoardHealthResponse(**fetch_org_board_health(conn))


@router.get("/orgs/{org_id}", response_model=OrgDetail)
def get_org(org_id: str, conn: Connection = Depends(get_conn)) -> OrgDetail:
    # F14 (2026-10-11): same as GET /v1/listings/{id} - o.id is a uuid, so a malformed path value raised
    # 22P02 and escaped as a 500 instead of this endpoint's own 404.
    try:
        row = conn.execute(
            f"""SELECT {_SUMMARY_SELECT}, o.career_urls, o.render_mode, o.robots_status, o.robots_checked_at,
                       o.openalex_id, o.openalex_works_count, o.qs_rank, o.the_rank
                FROM org_master o WHERE o.id = %(id)s""", {"id": org_id},
        ).fetchone()
    except psycopg.errors.DataError:
        raise HTTPException(status_code=404, detail="org not found") from None
    if row is None:
        raise HTTPException(status_code=404, detail="org not found")
    return OrgDetail(**{**dict(row), "id": str(row["id"])})
