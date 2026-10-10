"""GET /v1/governance - T20's own governance dashboard: the discovery state-machine funnel, per-host
discovery activity in the last 24h, and robots-denied counts. Delegates entirely to ajp.discovery's own
queries (governance_snapshot/coverage_report) - never a second definition of the same numbers.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from psycopg import Connection

from ajp import discovery
from ajp.api.deps import get_conn

router = APIRouter(prefix="/v1", tags=["governance"])


@router.get("/governance")
def governance(conn: Connection = Depends(get_conn)) -> Dict[str, Any]:
    return discovery.governance_snapshot(conn)


@router.get("/coverage")
def coverage(conn: Connection = Depends(get_conn)) -> Dict[str, Any]:
    return discovery.coverage_report(conn)
