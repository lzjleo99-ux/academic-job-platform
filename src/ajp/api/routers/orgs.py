"""GET /v1/orgs, /v1/orgs/{id} - packet 007 T14_fastapi."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import psycopg.errors
from psycopg import Connection

from ajp.api.deps import get_conn
from ajp.api.schemas import OrgDetail, OrgSummary

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
