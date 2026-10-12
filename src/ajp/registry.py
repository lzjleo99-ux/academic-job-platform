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


# The org-fan-out source: ONE source row that crawls every eligible RS organisation's own board, so its
# aggregate health hides per-org reality (PM's own F4 in debug_sweep_2026-10-10.md: "partially degraded,
# 3 sub-URLs tripped R1" - with no way to tell WHICH three from the API).
_ORG_BOARD_SOURCE_ID = "rs_org_boards"


def fetch_org_board_health(conn: psycopg.Connection) -> Dict[str, Any]:
    """Per-organisation health for the rs_org_boards fan-out, attributed by org slug.

    The data has existed since T21/F8: `_run_rs_org_boards` persists board.crawl_orgs()'s own per-org
    reports into `run_ledger.http_counters` as `{"orgs": N, "per_org": [...]}`, each entry already
    carrying the org's slug, its rows_new/rows_updated, its `blocked` list of `{url, reason, history}`,
    and whether it was degraded to a deeplink. What never existed was a reader: `fetch_source_statuses`
    aggregates crawl_state by source_id, so rs_org_boards reported one rolled-up status and a list of
    reason strings with no attribution - the exact shape of a real problem being reported honestly but
    unactionably.

    This reads that one column instead of adding a second source of truth. No new table, no per-org
    source rows, no change to scheduling or cadence - the scheduler's fan-out design is deliberate
    (one polite client, one manifest, one run per cycle over ~68 orgs) and is not what needs fixing.

    Same row_factory independence as fetch_source_statuses: called from the API (dict rows) and from
    tools/scripts (tuple rows) alike, so it pins its own cursor. `http_counters` is jsonb, which
    psycopg normally decodes for us - but a caller that disabled jsonb loading would hand back a str,
    so it is handled both ways rather than silently returning an empty health report.
    """
    cur = conn.cursor(row_factory=dict_row)
    row = cur.execute(
        """SELECT started_at, finished_at, status, http_counters FROM run_ledger
           WHERE source_id = %s AND http_counters IS NOT NULL
           ORDER BY started_at DESC LIMIT 1""",
        (_ORG_BOARD_SOURCE_ID,),
    ).fetchone()

    empty: Dict[str, Any] = {
        "source_id": _ORG_BOARD_SOURCE_ID, "run_started_at": None, "run_finished_at": None,
        "run_status": None, "orgs_total": 0, "orgs_ok": 0, "orgs_degraded": 0,
        "orgs_zero_rows": 0, "orgs": [],
    }
    if row is None:
        return empty

    counters = row["http_counters"]
    if isinstance(counters, str):
        counters = json.loads(counters) if counters.strip() else {}
    counters = counters or {}
    per_org = counters.get("per_org") or []

    orgs, degraded, zero_rows = [], 0, 0
    for rep in per_org:
        blocked = [
            {"url": b.get("url"), "reason": b.get("reason")}
            for b in (rep.get("blocked") or []) if isinstance(b, dict)
        ]
        to_deeplink = bool(rep.get("degraded_to_deeplink"))
        # An org is degraded when ANY of its board URLs is blocked, or when every one of them was and
        # it was demoted to a deeplink - the two states board.py itself distinguishes, kept distinct
        # here rather than collapsed into one boolean that loses which one happened.
        status = "degraded" if (blocked or to_deeplink) else "ok"
        if status == "degraded":
            degraded += 1
        if not (rep.get("rows_new") or rep.get("rows_updated")):
            zero_rows += 1
        orgs.append({
            "slug": rep.get("slug"),
            "status": status,
            "rows_new": int(rep.get("rows_new") or 0),
            "rows_updated": int(rep.get("rows_updated") or 0),
            "ingested": int(rep.get("ingested") or 0),
            "items": int(rep.get("items") or 0),
            "pages_fetched": int(rep.get("pages_fetched") or 0),
            "blocked": blocked,
            "degraded_to_deeplink": to_deeplink,
        })

    orgs.sort(key=lambda o: (o["status"] != "degraded", o["slug"] or ""))
    return {
        "source_id": _ORG_BOARD_SOURCE_ID,
        "run_started_at": row["started_at"], "run_finished_at": row["finished_at"],
        "run_status": row["status"],
        "orgs_total": len(orgs), "orgs_ok": len(orgs) - degraded,
        "orgs_degraded": degraded, "orgs_zero_rows": zero_rows, "orgs": orgs,
    }
