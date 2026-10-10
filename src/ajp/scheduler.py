"""T21: the scheduler/run-ledger layer - one shared factory (build_connector) mapping any `source` row to
a real connector instance (or a labelled no-op for a deeplink/detection-only/org-fan-out source), plus
due_sources()/run_once()/run_daemon() built on top of it. The SAME factory is meant to be reused by any dev
tool that wants "give me the real connector for this row" (never a second definition of the mapping).

Ledger evidence discipline: every counter written to run_ledger (pages_fetched/rows_new/rows_updated) comes
from the connector's own real return objects (crawl()'s own report dict, upsert_listing_deduped's own
per-row action) - never estimated or re-derived after the fact.
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import logging
import os
import signal
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row

from ajp.connectors.academicpositions import AcademicPositionsConnector
from ajp.connectors.academictransfer import AcademicTransferConnector
from ajp.connectors.ats_greenhouse import GreenhouseConnector
from ajp.connectors.ats_lever import LeverConnector
from ajp.connectors.ats_workday import WorkdayCxsConnector, WorkdaySite
from ajp.connectors.base import FetchFailed, ObjectStore, PoliteClient
from ajp.connectors.cern import CernConnector
from ajp.connectors.cnrs import CnrsConnector
from ajp.connectors.gen_sitemap_html import SitemapBoardConfig, SitemapHtmlConnector
from ajp.connectors.gen_wordpress import WordPressBoardConfig, WordPressConnector
from ajp.connectors.higher_ed_jobsrss import HigherEdJobsRssConnector, JobsRssConfig
from ajp.connectors.inria import InriaConnector
from ajp.connectors.jobs_ac_uk import JobsAcUkConnector
from ajp.connectors.kth import KthConnector
from ajp.connectors.ku_dk import KuDkConnector
from ajp.connectors.nature_careers import NatureCareersConnector
from ajp.connectors.nsz import NszScheduledConnector
from ajp.connectors.sljaka import SljakaConnector
from ajp.ingest import upsert_listing_deduped

log = logging.getLogger(__name__)

JITTER_FRACTION = 0.10        # R4: cadence 24h +/-10%
FX_STALE_HOURS = 72

# Every bespoke, fixed-source_id connector this project has built - each takes just (client, store),
# confirmed by grep across every one of these modules (none define a custom __init__ beyond Connector's own).
_BESPOKE_BY_SOURCE_ID: Dict[str, type] = {
    "academicpositions": AcademicPositionsConnector,
    "academictransfer": AcademicTransferConnector,
    "cern": CernConnector,
    "cnrs": CnrsConnector,
    "inria": InriaConnector,
    "jobs_ac_uk": JobsAcUkConnector,
    "kth": KthConnector,
    "ku_dk": KuDkConnector,
    "nature_careers": NatureCareersConnector,
    "nsz": NszScheduledConnector,        # F8: the bare-crawl()-shape scheduler adapter, not the raw NszConnector
    "sljaka": SljakaConnector,
}

# ats_detect.py's own detection-only families (T18's do-not-build list) - a real match on an org's own page
# is recorded, but no ingestion connector exists for any of these; a source row carrying one of these values
# would itself be a bug (detection alone should never register a source), reported cleanly rather than
# crashing either way.
_DETECTION_ONLY_ATS_FAMILIES = frozenset({
    "ats_smartrecruiters", "ats_teamtailor", "ats_reachmee_varbi", "ats_successfactors",
    "ats_taleo", "ats_icims", "ats_jobteaser",
})


@dataclasses.dataclass(frozen=True)
class NoOpConnector:
    """What build_connector() returns for a source with no connector to actually run (deeplink channel,
    detection-only ATS family, or the org-fan-out rs_org_boards source, driven separately per-org via
    board.crawl_orgs()) - never fetches anything; the scheduler recognises this type and records `reason`
    on the run_ledger row's own note instead of attempting a crawl."""
    source_id: str
    reason: str


def _coerce_config_fields(cls: type, config: Dict[str, Any]) -> Dict[str, Any]:
    """jsonb round-trips a tuple-typed dataclass field as a JSON array, deserialised as a plain list -
    coerced back to a tuple here. A key genuinely unknown to the dataclass (schema drift) is dropped rather
    than raising a TypeError, so an older config still constructs against today's class."""
    field_names = {f.name for f in dataclasses.fields(cls)}
    return {k: (tuple(v) if isinstance(v, list) else v) for k, v in config.items() if k in field_names}


def build_connector(source_row: Dict[str, Any], client: PoliteClient, store: ObjectStore):
    """The single shared mapping from a `source` row's own persisted identity (id/kind/ats_family/
    ats_tenant/ats_site/config) to a real connector instance. Never guesses: every value used is something
    an earlier task's own ensure_source()/discovery already wrote to this row, nothing invented here."""
    sid, kind, family = source_row["id"], source_row["kind"], source_row.get("ats_family")
    if kind == "channel":
        return NoOpConnector(sid, "deeplink channel - never fetched")
    if sid == "rs_org_boards":
        return NoOpConnector(sid, "org-fan-out source - driven per-org via board.crawl_orgs(), "
                                  "not a single connector instance")
    if sid in _BESPOKE_BY_SOURCE_ID:
        return _BESPOKE_BY_SOURCE_ID[sid](client, store)
    if family == "ats_workday_cxs":
        cfg = source_row.get("config") or {}
        site = WorkdaySite(tenant=source_row["ats_tenant"], wd_host=cfg["wd_host"], site=source_row["ats_site"])
        return WorkdayCxsConnector(client, store, site)
    if family == "ats_lever":
        return LeverConnector(client, store, source_row["ats_tenant"])
    if family == "ats_greenhouse":
        return GreenhouseConnector(client, store, source_row["ats_tenant"])
    if family == "gen_wordpress":
        cfg = WordPressBoardConfig(**_coerce_config_fields(WordPressBoardConfig, source_row.get("config") or {}))
        return WordPressConnector(client, store, cfg)
    if family == "gen_sitemap_html":
        cfg = SitemapBoardConfig(**_coerce_config_fields(SitemapBoardConfig, source_row.get("config") or {}))
        return SitemapHtmlConnector(client, store, cfg)
    if family == "higher_ed_jobsrss":
        cfg = JobsRssConfig(**_coerce_config_fields(JobsRssConfig, source_row.get("config") or {}))
        return HigherEdJobsRssConnector(client, store, cfg)
    if family in _DETECTION_ONLY_ATS_FAMILIES:
        return NoOpConnector(sid, f"{family} is detection-only - no ingestion connector exists")
    return NoOpConnector(sid, f"unrecognised source shape (kind={kind}, ats_family={family}) - "
                              f"no connector registered")


# --------------------------------------------------------------------- due-source computation (R4) -----------
def jitter_fraction(source_id: str, day: date) -> float:
    """Deterministic +/-10% jitter, stable for a given (source, calendar day) pair - the SAME source
    computes the SAME jitter every time this is called on the same day, a different one the next day.
    A plain hash, not a guess or a random draw: identical inputs always give the identical output, which is
    what "deterministic" means in the ruling (R4) this implements."""
    h = hashlib.sha256(f"{source_id}:{day.isoformat()}".encode()).digest()
    frac = int.from_bytes(h[:4], "big") / 0xFFFFFFFF        # [0, 1]
    return (frac * 2 - 1) * JITTER_FRACTION                  # [-JITTER_FRACTION, +JITTER_FRACTION]


def effective_cadence(source_row: Dict[str, Any], *, now: datetime) -> timedelta:
    frac = jitter_fraction(source_row["id"], now.date())
    return timedelta(hours=source_row["cadence_hours"] * (1 + frac))


def due_sources(conn: psycopg.Connection, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Every enabled, non-channel source whose last completed run (any status - a persistent failure's
    OWN handling is crawl_state's separate R1 counter, not this cadence) is far enough in the past, or that
    has never run at all. A row reconcile_stale_runs() closed out (note=_INTERRUPTED_NOTE) is excluded from
    "last completed run" here on purpose: an interrupted attempt never actually ran to completion, so it
    must not defer the source's next real attempt by a full cadence period - found live while testing this
    (F4, T21 PM acceptance audit): without this exclusion, reconciling a stale row made the source look like
    it had JUST successfully finished, the opposite of the intended "give it a fresh chance now" effect.
    Uses its own dict_row cursor regardless of the connection's default row_factory - same convention as
    registry.py/discovery.py, for the same reason (the shared test `conn` fixture defaults to tuples; the
    API layer's own connections default to dict_row)."""
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor(row_factory=dict_row)
    rows = cur.execute(
        """SELECT s.*, r.last_finished_at FROM source s
           LEFT JOIN (SELECT source_id, max(finished_at) AS last_finished_at FROM run_ledger
                     WHERE finished_at IS NOT NULL AND note IS DISTINCT FROM %s
                     GROUP BY source_id) r ON r.source_id = s.id
           WHERE s.enabled AND s.kind != 'channel'""",
        (_INTERRUPTED_NOTE,),
    ).fetchall()
    due = []
    for row in rows:
        row = dict(row)
        last = row.pop("last_finished_at")
        if last is None or last + effective_cadence(row, now=now) <= now:
            due.append(row)
    return due


# --------------------------------------------------------------------- FX-first (packet's own ordering rule) --
def run_fx_first(dsn: str, *, raw_root: Optional[str] = None) -> Dict[str, Any]:
    """Every ajp-run --once/--daemon cycle snapshots FX BEFORE running any source, so a source that reads
    fx_rate_latest mid-run (salary EUR/year conversion) never sees a stale rate this same run could have
    refreshed. One provider failing never stops the other (ajp.fx.job's own contract) or the sources that
    follow. Returns the providers' own results plus a >72h staleness warning, never fabricated."""
    from ajp.fx import ecb, erapi
    from ajp.rawstore import LocalFsStore

    store = LocalFsStore(raw_root or os.environ.get("RAW_STORE_ROOT", "data/raw"))
    polite = PoliteClient()
    out: Dict[str, Any] = {"providers": {}, "stale_warning": None}
    for name, fn in (("ecb", ecb.run), ("erapi", erapi.run)):
        try:
            out["providers"][name] = fn(dsn, store, polite)
        except Exception as e:  # noqa: BLE001 - one provider failing must never stop the other or the run
            out["providers"][name] = {"error": f"{type(e).__name__}: {e}"}
    with psycopg.connect(dsn, autocommit=True) as conn:
        as_of = conn.execute("SELECT max(rate_date) FROM fx_rate_latest").fetchone()[0]
    if as_of is not None:
        age_hours = (datetime.now(timezone.utc).date() - as_of).days * 24
        if age_hours > FX_STALE_HOURS:
            out["stale_warning"] = f"FX snapshot is {age_hours}h old (> {FX_STALE_HOURS}h threshold), as_of={as_of}"
    else:
        out["stale_warning"] = "no FX snapshot exists yet"
    return out


# --------------------------------------------------------------------- one source, one ledger row -------------
def _discover_and_crawl(connector) -> Any:
    """Real inconsistency across this project's own connectors, found live building this factory: most
    expose discover() then crawl(items), but several (sljaka/ats_lever/ats_greenhouse) do their own
    internal discovery and take crawl() with NO items argument at all, and three more
    (ats_workday/gen_wordpress/gen_sitemap_html) make `items` OPTIONAL on crawl() (falling back to their
    own discover() when omitted) precisely so they can be called either way. Rather than a per-family
    special case (which would silently go stale the next time a connector's own shape changes), this
    inspects crawl()'s REAL signature: if it can be called with zero arguments, it is (works for all of the
    shapes above); only when crawl() truly requires an items argument does this call discover() first."""
    sig = inspect.signature(connector.crawl)
    needs_items = any(
        p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        for p in sig.parameters.values()
    )
    if needs_items:
        return connector.crawl(connector.discover())
    return connector.crawl()


# F9 (found live, 2026-09-28): a real, reproducible hang against academictransfer.com surfaced during this
# session's own real-data verification (confirmed 3x independently - every hung process needed SIGKILL,
# SIGTERM was ignored every time, the clearest sign of a genuine hang rather than slow polling). Root cause
# was never fully pinned down despite thorough live investigation (a direct, unthrottled scan of every one of
# its ~700 real detail URLs came back clean; the real PoliteClient/RobotsCache combination worked correctly
# in isolation too) - it reproduces only inside the long-running scheduler process, not in any isolated
# reproduction attempt. Rather than leave the whole scheduler vulnerable to ANY future unexplained hang
# (whatever its ultimate cause, on this host or a different one later), a per-source wall-clock watchdog now
# bounds how long a single source's discover()+crawl() may run: academictransfer's own docstring documents
# ~110 minutes as its expected full-sweep duration at the site's real Crawl-delay:10, so the ceiling here is
# generous enough never to abort a legitimately slow, polite full sweep, while still guaranteeing no single
# source can block every OTHER due source, or the whole `--once`/`--daemon` invocation, indefinitely.
MAX_SOURCE_RUNTIME_SECONDS = 3 * 3600  # 3 hours


def _discover_and_crawl_watchdog(connector) -> Any:
    """Runs _discover_and_crawl in a background thread with a hard wall-clock ceiling. Python cannot forcibly
    kill a thread - a source that times out leaves its worker thread running until it eventually finishes or
    the process exits, not truly terminated - but the calling scheduler is unblocked immediately either way,
    which is the actual guarantee this exists for.

    A raw daemon `threading.Thread`, not `concurrent.futures.ThreadPoolExecutor`, is used deliberately: found
    live writing this fix's own test - an executor's worker threads are NOT daemon threads, so a leaked,
    permanently-hung one (exactly what this exists to survive) makes the WHOLE PROCESS's own interpreter
    shutdown hang forever waiting to join it, even though `run_once()` itself already returned - the exact
    same class of hang this is meant to prevent, just moved to process-exit time instead of mid-run. A daemon
    thread carries no such join obligation: the interpreter simply abandons it on exit, whether that's this
    module's own test suite or a real `ajp-run --once` invocation."""
    box: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["result"] = _discover_and_crawl(connector)
        except BaseException as e:  # noqa: BLE001 - re-raised in the calling thread, not swallowed
            box["error"] = e

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=MAX_SOURCE_RUNTIME_SECONDS)
    if thread.is_alive():
        raise FetchFailed(
            f"exceeded the {MAX_SOURCE_RUNTIME_SECONDS}s per-source watchdog deadline - a real hang, not "
            "normal slow crawling (F9)")
    if "error" in box:
        raise box["error"]
    return box["result"]


_R1_KEY = "__scheduler__"    # a single logical key per source, distinct from board.py's own per-URL R1 keys


def _run_rs_org_boards(conn: psycopg.Connection, client: PoliteClient, store: ObjectStore, *,
                       run_id: Any, r1: Any, run_id_for_r1: str, finished_at: datetime) -> Dict[str, Any]:
    """F8 (PM live audit 2026-09-24): rs_org_boards is the org-fan-out source (board.crawl_orgs(), one
    BoardConnector per eligible RS organisation) - it has no single connector instance the generic factory
    could ever return, so it never went through build_connector()/_discover_and_crawl() at all, and (found
    live) was NEVER ACTUALLY CALLED from the real scheduler either - only a labelled NoOpConnector, with
    crawl_orgs() itself invoked only from tests/tools. Wired here as its own branch instead: fetch the
    sanctioned org list (ajp.orgs.crawl_targets - robots-allowed, a fetch mode that performs requests, a
    real career URL), run crawl_orgs() for real, and aggregate its own per-org reports into one ledger row
    for the whole source. Each org's own per-URL failures are already isolated inside BoardConnector.crawl()
    itself (unrelated to F7 - this was already fault-tolerant); this function's own job is just wiring it
    into the ledger/R1 machinery every other source already gets."""
    from ajp.connectors.board import crawl_orgs
    from ajp.orgs import crawl_targets

    org_list = crawl_targets(conn)
    if not org_list:
        r1.ok(_R1_KEY)
        note = "no orgs eligible for direct crawl (render_mode/robots_status/career_urls) - a real, honest state"
        conn.execute(
            """UPDATE run_ledger SET finished_at = %s, status = 'ok', pages_fetched = 0,
                      rows_new = 0, rows_updated = 0, note = %s WHERE run_id = %s""",
            (finished_at, note, run_id),
        )
        return {"source_id": "rs_org_boards", "run_id": run_id, "status": "ok", "note": note}
    reports = crawl_orgs(conn, client, store, org_list, run_id_for_r1)
    pages_fetched = sum(rep.get("pages_fetched", 0) for rep in reports)
    rows_new = sum(rep.get("rows_new", 0) for rep in reports)
    rows_updated = sum(rep.get("rows_updated", 0) for rep in reports)
    r1.ok(_R1_KEY)      # per-org failures are tracked by board.py's own per-URL CrawlState, not this key
    conn.execute(
        """UPDATE run_ledger SET finished_at = %s, status = 'ok', pages_fetched = %s,
                  rows_new = %s, rows_updated = %s, http_counters = %s WHERE run_id = %s""",
        (finished_at, pages_fetched, rows_new, rows_updated,
         json.dumps({"orgs": len(reports), "per_org": reports}, default=str), run_id),
    )
    return {"source_id": "rs_org_boards", "run_id": run_id, "status": "ok",
           "rows_new": rows_new, "rows_updated": rows_updated, "orgs": len(reports)}


def _run_one_source(conn: psycopg.Connection, client: PoliteClient, store: ObjectStore,
                    source_row: Dict[str, Any], *, started_at: datetime, run_id_for_r1: str) -> Dict[str, Any]:
    """Isolated: any exception here (a connector bug, a real network failure the connector's own retries
    gave up on, anything) is caught, logged to THIS source's own run_ledger row, and never propagates - the
    packet's own 'one failure logged, never stops others' contract. R1 (crawl_state.py's own 3-consecutive-
    failed-runs counter, already used per-URL inside board.py) is reused here at the SOURCE level: the 3rd
    consecutive failed run_ledger row for this source is logged status='blocked' instead of plain 'error' -
    the same counter, a different key, so the two never collide or double-count each other.

    `finished_at` is stamped as `started_at` (T26, found live 2026-09-25 running the offline suite the day
    after writing it): every UPDATE here used to stamp `finished_at = now()`, the database SERVER's own real
    wall-clock time, while due_sources()'s own cadence check compares against the `now` the CALLER passed to
    run_once() - identical in real ajp-run usage (both really are 'now'), but a test that simulates a fixed
    'now' far from the real wall clock (exactly what happens the day after such a test was written, if 'now'
    is a hardcoded constant) got a silently wrong 'is this source due yet' answer. Both timestamps are now
    the one, single 'now' the whole call already reasons about - never two independent clocks that can
    quietly drift apart."""
    from ajp.crawl_state import CrawlState

    cur = conn.cursor(row_factory=dict_row)
    run_id = cur.execute(
        "INSERT INTO run_ledger (source_id, started_at) VALUES (%s, %s) RETURNING run_id",
        (source_row["id"], started_at),
    ).fetchone()["run_id"]
    r1 = CrawlState(conn, source_row["id"], run_id_for_r1)
    connector = None
    try:
        if source_row["id"] == "rs_org_boards":
            return _run_rs_org_boards(conn, client, store, run_id=run_id, r1=r1, run_id_for_r1=run_id_for_r1,
                                      finished_at=started_at)
        connector = build_connector(source_row, client, store)
        if isinstance(connector, NoOpConnector):
            r1.ok(_R1_KEY)
            conn.execute(
                """UPDATE run_ledger SET finished_at = %s, status = 'ok', pages_fetched = 0,
                          rows_new = 0, rows_updated = 0, note = %s WHERE run_id = %s""",
                (started_at, connector.reason, run_id),
            )
            return {"source_id": source_row["id"], "run_id": run_id, "status": "ok", "note": connector.reason}
        rows, report = _discover_and_crawl_watchdog(connector)
        rows_new = rows_updated = 0
        for row in rows:
            _, action = upsert_listing_deduped(conn, row, store)
            if action == "inserted":
                rows_new += 1
            else:
                rows_updated += 1
        r1.ok(_R1_KEY)
        conn.execute(
            """UPDATE run_ledger SET finished_at = %s, status = 'ok', pages_fetched = %s,
                      rows_new = %s, rows_updated = %s, http_counters = %s WHERE run_id = %s""",
            (started_at, report.get("attempted", len(rows)), rows_new, rows_updated,
             json.dumps(report, default=str), run_id),
        )
        return {"source_id": source_row["id"], "run_id": run_id, "status": "ok",
               "rows_new": rows_new, "rows_updated": rows_updated}
    except Exception as e:  # noqa: BLE001 - isolation is the whole point; every exception is caught here
        log.warning("source %s failed: %s: %s", source_row["id"], type(e).__name__, e)
        error_text = f"{type(e).__name__}: {e}"
        r1_state = r1.fail(_R1_KEY, error_text)
        status = "blocked" if r1_state["blocked_reason"] else "error"
        conn.execute(
            "UPDATE run_ledger SET finished_at = %s, status = %s, note = %s WHERE run_id = %s",
            (started_at, status, error_text if status == "error" else r1_state["blocked_reason"], run_id),
        )
        return {"source_id": source_row["id"], "run_id": run_id, "status": status, "error": error_text}
    finally:
        # Connector's own base class has no close() at all - only board.py's BoardConnector defines one
        # (it owns a renderer instance); most connectors have nothing of their own to release (the shared
        # PoliteClient is the caller's responsibility, closed once for the whole run, never per-source).
        close = getattr(connector, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - closing must never mask the real result above
                pass


STALE_RUN_THRESHOLD_HOURS = 1
_INTERRUPTED_NOTE = "interrupted (process terminated before this run finished)"


def reconcile_stale_runs(conn: psycopg.Connection, *, now: Optional[datetime] = None,
                         older_than_hours: float = STALE_RUN_THRESHOLD_HOURS) -> int:
    """PM found live (2026-09-24, T21 acceptance audit): a SIGTERM mid-source left a run_ledger row with
    finished_at still NULL forever - dangling, never counted by due_sources (which only reads rows WHERE
    finished_at IS NOT NULL, so it is harmlessly ignored for scheduling, but stays visibly wrong in the
    ledger). Closed out here as status='error' with a note naming the real cause, reusing the existing
    status enum rather than a new migration adding a distinct 'interrupted' value - it IS an error (the run
    never completed), and the note already says why; a fourth status would be a real state a lot of other
    code (this module's own status-in ('ok','error','blocked') checks, any future dashboard query) would
    then have to learn about for no behavioural difference from 'error'. Called at the start of every
    run_once() (so run_daemon() gets it automatically too, once per cycle)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=older_than_hours)
    cur = conn.execute(
        "UPDATE run_ledger SET finished_at = %s, status = 'error', note = %s "
        "WHERE finished_at IS NULL AND started_at < %s",
        (now, _INTERRUPTED_NOTE, cutoff),
    )
    return cur.rowcount


def run_once(conn: psycopg.Connection, client: PoliteClient, store: ObjectStore, dsn: str, *,
            now: Optional[datetime] = None, skip_fx: bool = False) -> Dict[str, Any]:
    """FX first, then every due source, isolated. `skip_fx` exists only for offline tests (no real network
    call for real exchange rates in a unit test) - ajp-run's own CLI never sets it. SIGTERM is checked
    between sources (never mid-source) so a long --once invocation killed partway through still leaves
    every source it did reach with a real, closed run_ledger row - the same discipline run_daemon() already
    had, now also covering a single --once call over a large due-source set."""
    now = now or datetime.now(timezone.utc)
    reconcile_stale_runs(conn, now=now)
    fx_result = None if skip_fx else run_fx_first(dsn)
    targets = due_sources(conn, now=now)
    run_id_for_r1 = now.isoformat()

    stop = {"flag": False}

    def _on_sigterm(signum, frame):  # noqa: ARG001 - signal handler signature
        stop["flag"] = True

    old_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _on_sigterm)
    results = []
    try:
        for row in targets:
            if stop["flag"]:
                break
            results.append(_run_one_source(conn, client, store, row, started_at=now, run_id_for_r1=run_id_for_r1))
    finally:
        signal.signal(signal.SIGTERM, old_handler)
    return {"fx": fx_result, "sources_due": len(targets), "sources_run": len(results),
           "interrupted": stop["flag"], "results": results}


def run_daemon(conn: psycopg.Connection, client: PoliteClient, store: ObjectStore, dsn: str, *,
              sleep: Callable[[float], None] = None, clock: Callable[[], datetime] = None,
              poll_seconds: float = 60.0, max_cycles: Optional[int] = None, skip_fx: bool = False) -> None:
    """Loops run_once() calls, sleeping poll_seconds between checks for a newly-due source - no smarter
    "sleep exactly until the next due time" scheduling is attempted (every source's own jitter is
    recomputed fresh each cycle anyway, per jitter_fraction's own day-keyed determinism, so there is no
    single "next due" instant to sleep until that would stay correct across a day boundary). SIGTERM sets a
    flag checked between cycles - never mid-source - so an in-flight source's own run_ledger row still gets
    a real finished_at/status before the process exits, not left dangling as started_at-only forever.
    `max_cycles` is for tests only (a real daemon runs until SIGTERM)."""
    import time as _time
    sleep = sleep or _time.sleep
    clock = clock or (lambda: datetime.now(timezone.utc))

    stop = {"flag": False}

    def _on_sigterm(signum, frame):  # noqa: ARG001 - signal handler signature
        stop["flag"] = True

    old_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        cycles = 0
        while not stop["flag"]:
            run_once(conn, client, store, dsn, now=clock(), skip_fx=skip_fx)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            sleep(poll_seconds)
    finally:
        signal.signal(signal.SIGTERM, old_handler)
