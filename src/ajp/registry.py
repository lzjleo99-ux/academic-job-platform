"""T18/T19 reporting artifact: a per-source inventory (id, kind, detected ATS family/tenant/site, TDM
reservation, robots state, geo scope, last-run status, rows ingested, degraded reasons) - the single query
GET /v1/sources (ajp.api.routers.sources) exposes over the API, reused here to also write the flat
pm/outbox/sources.json file packet 009 asks for. One definition of what a source's own health/identity looks
like, never two that could drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row

_SOURCE_COLUMNS = ("id", "kind", "ats_family", "ats_tenant", "ats_site", "tdm_reservation", "robots_ok",
                   "geo_scope", "base_url", "list_url", "notes")


def fetch_source_statuses(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """A 'channel' source has no listing rows by construction (deeplink sources are never ingested, D-rule)
    and no crawl_state either (never fetched) - it always reports last_run_status='not_applicable'.

    Uses its own dict_row cursor regardless of the connection's default row_factory - this module is called
    both from the API layer (which already sets dict_row on its own connections) and from plain scripts/
    tools (which don't), and must behave identically either way rather than silently relying on the caller's
    own setup (found live: the shared test `conn` fixture uses tuple rows by default).

    T24 compliance dashboard: requests_24h and denied_24h come from run_ledger (T21's own real evidence
    trail, never a separate/estimated counter) - requests_24h sums pages_fetched across every run started in
    the last 24h; denied_24h counts runs in that same window whose own note records a real RobotsDenied or
    ManifestViolation (PoliteClient.check() refuses a disallowed/out-of-manifest URL BEFORE ever sending the
    request, so this is architecturally always 0 unless something bypassed that gate - reported as a real,
    queried number rather than just asserted, so a genuine regression would actually show up here)."""
    cur = conn.cursor(row_factory=dict_row)
    sources = cur.execute(f"SELECT {', '.join(_SOURCE_COLUMNS)} FROM source ORDER BY id").fetchall()
    runs = {r["source_id"]: r for r in cur.execute(
        """SELECT source_id, max(last_attempt_at) AS last_attempt_at, max(last_success_at) AS last_success_at,
                  array_remove(array_agg(DISTINCT blocked_reason), NULL) AS blocked_reasons
           FROM crawl_state GROUP BY source_id"""
    ).fetchall()}
    counts = {r["source_id"]: r["n"] for r in cur.execute(
        "SELECT source_id, count(*) AS n FROM listing GROUP BY source_id"
    ).fetchall()}
    ledger_24h = {r["source_id"]: r for r in cur.execute(
        """SELECT source_id, coalesce(sum(pages_fetched), 0) AS requests_24h,
                  count(*) FILTER (WHERE note ILIKE '%%RobotsDenied%%' OR note ILIKE '%%ManifestViolation%%') AS denied_24h
           FROM run_ledger WHERE started_at > now() - interval '24 hours' GROUP BY source_id"""
    ).fetchall()}
    out = []
    for s in sources:
        row = dict(s)
        sid = row["id"]
        run = runs.get(sid)
        ledger = ledger_24h.get(sid)
        if row["kind"] == "channel":
            status = "not_applicable"
        elif run is None:
            status = "never_run"
        elif run["blocked_reasons"]:
            status = "degraded"
        else:
            status = "ok"
        row.update(
            last_run_status=status, last_attempt_at=run["last_attempt_at"] if run else None,
            last_success_at=run["last_success_at"] if run else None,
            listing_count=counts.get(sid, 0), degraded_reasons=list(run["blocked_reasons"]) if run else [],
            requests_24h=ledger["requests_24h"] if ledger else 0,
            denied_24h=ledger["denied_24h"] if ledger else 0,
        )
        out.append(row)
    return out


def write_sources_json(conn: psycopg.Connection, path: Path) -> Path:
    data = fetch_source_statuses(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    return path
