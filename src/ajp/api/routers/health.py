"""GET /health - packet 007: db, fx freshness, sources last-run counts."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from psycopg import Connection

from ajp.api.deps import get_conn
from ajp.api.schemas import HealthResponse
from ajp.scheduler import fx_stale_warning as fx_stale_warning_for

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(conn: Connection = Depends(get_conn)) -> HealthResponse:
    conn.execute("SELECT 1")
    fx = conn.execute("SELECT max(rate_date) AS d, count(DISTINCT currency) AS n FROM fx_rate_latest").fetchone()
    runs = conn.execute(
        """SELECT source_id, max(last_attempt_at) AS last_attempt_at, max(last_success_at) AS last_success_at,
                  count(*) FILTER (WHERE blocked_reason IS NOT NULL) AS blocked_keys, count(*) AS total_keys
           FROM crawl_state GROUP BY source_id ORDER BY source_id"""
    ).fetchall()
    degraded = [r["source_id"] for r in runs if r["blocked_keys"] > 0]
    # T21 (R4/scheduler): the SAME >72h staleness threshold ajp.scheduler.run_fx_first checks before every
    # scheduled run - and now literally the same function, not a copy of the same arithmetic, so /health and
    # the ledger's own warning cannot drift apart about what "stale" means (F14).
    fx_stale_warning = fx_stale_warning_for(conn)
    return HealthResponse(
        status="ok", db="ok", fx_as_of=fx["d"], fx_currencies=fx["n"] or 0,
        sources_last_run=[dict(r) for r in runs], degraded_sources=degraded,
        fx_stale_warning=fx_stale_warning,
    )
