"""GET /v1/sources - packet 007 T14_fastapi (id, kind, route type, robots state, last run status/ts, counts,
degraded reasons) extended by packet 009 T18 (detected ATS family/tenant/site, TDM reservation). The actual
query lives in ajp.registry (shared with the pm/outbox/sources.json exporter) - this router is a thin
pydantic-shaping wrapper around it, never a second definition of the same data.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from psycopg import Connection

from ajp.api import grouping
from ajp.api.deps import get_conn
from ajp.api.schemas import SourceStatus
from ajp.registry import fetch_source_statuses

router = APIRouter(prefix="/v1", tags=["sources"])


@router.get("/sources", response_model=List[SourceStatus])
def list_sources(conn: Connection = Depends(get_conn)) -> List[SourceStatus]:
    return [
        SourceStatus(
            id=row["id"], kind=row["kind"], route_type=grouping.route_type_label(row["id"], row["ats_family"]),
            robots_ok=row["robots_ok"], last_run_status=row["last_run_status"],
            last_attempt_at=row["last_attempt_at"], last_success_at=row["last_success_at"],
            listing_count=row["listing_count"], degraded_reasons=row["degraded_reasons"],
            ats_family=row["ats_family"], ats_tenant=row["ats_tenant"], ats_site=row["ats_site"],
            tdm_reservation=row["tdm_reservation"], geo_scope=row["geo_scope"],
            requests_24h=row["requests_24h"], denied_24h=row["denied_24h"],
        )
        for row in fetch_source_statuses(conn)
    ]
