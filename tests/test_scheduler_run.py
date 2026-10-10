"""T21 acceptance: run_once/run_daemon/jitter/due_sources - offline, real MockTransport HTTP layer (not a
per-connector test double), so the FULL discover -> crawl -> upsert_listing_deduped -> run_ledger pipeline
is exercised exactly as ajp-run itself would run it."""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from ajp import scheduler
from ajp.connectors.base import PoliteClient
from ajp.connectors.cern import CernConnector
from ajp.rawstore import LocalFsStore

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Postdoctoral Research Fellow</title><link>https://careers.cern/jobs/postdoc-fellow/</link>
<pubDate>Fri, 11 Sep 2026 12:15:39 +0000</pubDate><description><![CDATA[]]></description></item>
</channel></rss>"""
DETAIL = """<html><body><main>
<div class="location">Geneva, Switzerland</div>
<article>We are seeking a postdoctoral research fellow to join our physics research programme.</article>
</main></body></html>"""


def mock_client():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/jobs/feed/":
            return httpx.Response(200, content=RSS.encode())
        if req.url.path == "/jobs/postdoc-fellow/":
            return httpx.Response(200, content=DETAIL.encode())
        return httpx.Response(404)

    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None)


# --------------------------------------------------------------------- jitter/cadence ---------------------
def test_jitter_fraction_is_deterministic_for_the_same_source_and_day():
    a = scheduler.jitter_fraction("cern", date(2026, 9, 24))
    b = scheduler.jitter_fraction("cern", date(2026, 9, 24))
    assert a == b
    assert -0.10 <= a <= 0.10


def test_jitter_fraction_differs_across_days_or_sources_in_general():
    a = scheduler.jitter_fraction("cern", date(2026, 9, 24))
    b = scheduler.jitter_fraction("cern", date(2026, 9, 25))
    c = scheduler.jitter_fraction("sljaka", date(2026, 9, 24))
    assert len({a, b, c}) == 3           # astronomically unlikely to collide by chance - a real hash, not a constant


def test_due_sources_includes_a_never_run_source_and_excludes_channels(conn):
    CernConnector.ensure_source(conn)
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('some_channel', 'channel', 'https://x.invalid', false)")
    due = scheduler.due_sources(conn, now=NOW)
    ids = {d["id"] for d in due}
    assert "cern" in ids and "some_channel" not in ids


def test_due_sources_excludes_a_source_run_recently_within_its_cadence(conn):
    CernConnector.ensure_source(conn)
    conn.execute("INSERT INTO run_ledger (source_id, started_at, finished_at, status) VALUES ('cern', %s, %s, 'ok')",
                 (NOW - timedelta(hours=1), NOW - timedelta(hours=1)))
    due = scheduler.due_sources(conn, now=NOW)                      # cadence_hours=24, run 1h ago - not due
    assert "cern" not in {d["id"] for d in due}


def test_due_sources_includes_a_source_whose_cadence_has_elapsed(conn):
    CernConnector.ensure_source(conn)
    conn.execute("INSERT INTO run_ledger (source_id, started_at, finished_at, status) VALUES ('cern', %s, %s, 'ok')",
                 (NOW - timedelta(hours=30), NOW - timedelta(hours=30)))
    due = scheduler.due_sources(conn, now=NOW)                      # 30h > 24h*(1.1 max) - due regardless of jitter
    assert "cern" in {d["id"] for d in due}


# --------------------------------------------------------------------- run_once end-to-end -----------------
def test_two_consecutive_runs_are_stable_second_run_adds_no_new_rows(conn, tmp_path):
    CernConnector.ensure_source(conn)
    client, store = mock_client(), LocalFsStore(tmp_path)

    result1 = scheduler.run_once(conn, client, store, "unused", now=NOW, skip_fx=True)
    assert result1["sources_due"] == 1
    assert result1["results"][0]["status"] == "ok"
    assert result1["results"][0]["rows_new"] == 1

    listing_count_1 = conn.execute("SELECT count(*) FROM listing").fetchone()[0]
    assert listing_count_1 == 1

    # second run, one cadence period later - same feed, same item: must be a refresh, not a duplicate.
    # +27h, not +25h (found live, T26, 2026-09-25): R4's own jitter is +/-10% of a 24h cadence, i.e. up to
    # 26.4h - a +25h step can land BEFORE the source is actually due again on a date whose deterministic
    # jitter happens to be positive (exactly cern's own real jitter for 2026-09-25), making this test's own
    # timing assumption source/date-dependent rather than a real guarantee. +27h clears the jitter window
    # regardless of which day's jitter applies.
    result2 = scheduler.run_once(conn, client, store, "unused", now=NOW + timedelta(hours=27), skip_fx=True)
    assert result2["sources_due"] == 1
    assert result2["results"][0]["rows_new"] == 0
    listing_count_2 = conn.execute("SELECT count(*) FROM listing").fetchone()[0]
    assert listing_count_2 == 1                                     # stable, not doubled

    ledger_rows = conn.execute("SELECT status FROM run_ledger WHERE source_id = 'cern' ORDER BY run_id").fetchall()
    assert [r[0] for r in ledger_rows] == ["ok", "ok"]


def test_a_failing_source_is_logged_as_error_and_never_raises_or_stops_others(conn, tmp_path):
    CernConnector.ensure_source(conn)
    conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope)
                    VALUES ('sljaka','feed','https://sljaka.com/','https://sljaka.com/feed/',true,1.0,'RS')""")
    # a client that 500s everything except cern's own feed/detail - sljaka's own run must fail in isolation
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "careers.cern":
            if req.url.path == "/jobs/feed/":
                return httpx.Response(200, content=RSS.encode())
            if req.url.path == "/jobs/postdoc-fellow/":
                return httpx.Response(200, content=DETAIL.encode())
        return httpx.Response(500)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000,
                          sleep=lambda s: None, max_retries=0)
    result = scheduler.run_once(conn, client, LocalFsStore(tmp_path), "unused", now=NOW, skip_fx=True)
    by_id = {r["source_id"]: r for r in result["results"]}
    assert by_id["sljaka"]["status"] == "error"
    assert by_id["cern"]["status"] == "ok" and by_id["cern"]["rows_new"] == 1     # cern's own run is unaffected

    statuses = dict(conn.execute("SELECT source_id, status FROM run_ledger").fetchall())
    assert statuses == {"sljaka": "error", "cern": "ok"}


# --------------------------------------------------------------------- F9: per-source watchdog --------------
class _NeverReturningConnector:
    """A stand-in for a real connector whose crawl() hangs past every request-level timeout (F9, found live
    2026-09-28 against academictransfer.com - reproduced 3x, root cause never fully pinned down despite
    thorough live investigation). threading.Event.wait() blocks the WORKER thread only - the watchdog's own
    timeout must still fire in the caller regardless."""
    def crawl(self):
        import threading
        threading.Event().wait()  # blocks forever; never actually reached by a real, bounded test run


def test_the_watchdog_fires_when_a_source_s_crawl_never_returns():
    import time
    import pytest as _pytest
    from ajp.connectors.base import FetchFailed
    from ajp import scheduler as sched

    original = sched.MAX_SOURCE_RUNTIME_SECONDS
    sched.MAX_SOURCE_RUNTIME_SECONDS = 0.2
    try:
        start = time.monotonic()
        with _pytest.raises(FetchFailed, match="watchdog deadline"):
            sched._discover_and_crawl_watchdog(_NeverReturningConnector())
        # the CALLER is unblocked at roughly the watchdog's own deadline, not left waiting on the hung thread
        assert time.monotonic() - start < 2.0
    finally:
        sched.MAX_SOURCE_RUNTIME_SECONDS = original


def test_a_watchdog_timeout_on_one_source_never_blocks_a_different_due_source_in_the_same_run(conn, tmp_path):
    """End-to-end: even a source whose crawl() never returns at all must not prevent run_once() from still
    completing and successfully processing every OTHER due source in the same pass."""
    from ajp import scheduler as sched

    CernConnector.ensure_source(conn)
    conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope)
                    VALUES ('sljaka','feed','https://sljaka.com/','https://sljaka.com/feed/',true,1.0,'RS')""")

    original_build = sched.build_connector
    def fake_build_connector(source_row, client, store):
        if source_row["id"] == "sljaka":
            return _NeverReturningConnector()
        return original_build(source_row, client, store)

    original_timeout = sched.MAX_SOURCE_RUNTIME_SECONDS
    sched.MAX_SOURCE_RUNTIME_SECONDS = 0.2
    sched.build_connector = fake_build_connector
    try:
        result = sched.run_once(conn, mock_client(), LocalFsStore(tmp_path), "unused", now=NOW, skip_fx=True)
    finally:
        sched.build_connector = original_build
        sched.MAX_SOURCE_RUNTIME_SECONDS = original_timeout

    by_id = {r["source_id"]: r for r in result["results"]}
    assert by_id["sljaka"]["status"] == "error" and "watchdog" in by_id["sljaka"]["error"]
    assert by_id["cern"]["status"] == "ok" and by_id["cern"]["rows_new"] == 1


# --------------------------------------------------------------------- run_daemon ---------------------------
def test_daemon_runs_three_sources_respecting_cadence_and_one_failure_never_stops_the_others(conn, tmp_path):
    CernConnector.ensure_source(conn)
    conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, cadence_hours)
                    VALUES ('sljaka','feed','https://sljaka.com/','https://sljaka.com/feed/',true,1.0,'RS', 48)""")
    conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope)
                    VALUES ('nsz','scrape_html','https://nsz.gov.rs/','https://nsz.gov.rs/',true,1.0,'RS')""")

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "careers.cern" and req.url.path == "/jobs/feed/":
            return httpx.Response(200, content=RSS.encode())
        if req.url.host == "careers.cern" and req.url.path == "/jobs/postdoc-fellow/":
            return httpx.Response(200, content=DETAIL.encode())
        return httpx.Response(500)          # sljaka and nsz always fail here - deliberately

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000,
                          sleep=lambda s: None, max_retries=0)
    # cern/nsz default to cadence_hours=24; the gap between cycles must exceed that (plus jitter) for both
    # to be due again on the second cycle - 1 hour (the original draft here) never would be.
    clock_values = [NOW, NOW + timedelta(hours=26)]

    def clock():
        return clock_values.pop(0) if clock_values else NOW + timedelta(hours=52)

    scheduler.run_daemon(conn, client, LocalFsStore(tmp_path), "unused", sleep=lambda s: None, clock=clock,
                        max_cycles=2, skip_fx=True)

    statuses = conn.execute("SELECT source_id, status FROM run_ledger ORDER BY run_id").fetchall()
    ids_seen = {s for s, _ in statuses}
    assert "cern" in ids_seen and "nsz" in ids_seen                  # both due every cycle (24h default)
    assert all(status in ("ok", "error") for _, status in statuses)  # every row got a real terminal status
    cern_statuses = [st for sid, st in statuses if sid == "cern"]
    assert cern_statuses == ["ok", "ok"]                             # cern succeeds every cycle, never stopped by nsz failing


# --------------------------------------------------------------------- F4: stale-run reconciliation (T21 PM audit) ---
def test_reconcile_stale_runs_closes_a_dangling_in_flight_row(conn):
    """PM found live (2026-09-24): a SIGTERM mid-source left a run_ledger row with finished_at NULL
    forever. A row still open more than the threshold after it started is closed out as status='error'
    with a note naming the cause; a row started RECENTLY (still genuinely in flight) is left alone."""
    CernConnector.ensure_source(conn)
    old_run_id = conn.execute("INSERT INTO run_ledger (source_id, started_at) VALUES ('cern', %s) RETURNING run_id",
                              (NOW - timedelta(hours=3),)).fetchone()[0]
    recent_run_id = conn.execute("INSERT INTO run_ledger (source_id, started_at) VALUES ('cern', %s) RETURNING run_id",
                                 (NOW - timedelta(minutes=5),)).fetchone()[0]
    n = scheduler.reconcile_stale_runs(conn, now=NOW, older_than_hours=1)
    assert n == 1
    old_row = conn.execute("SELECT status, finished_at, note FROM run_ledger WHERE run_id=%s", (old_run_id,)).fetchone()
    assert old_row[0] == "error" and old_row[1] is not None and "interrupted" in old_row[2]
    recent_row = conn.execute("SELECT status, finished_at FROM run_ledger WHERE run_id=%s", (recent_run_id,)).fetchone()
    assert recent_row == (None, None)                                # untouched - genuinely still in flight


def test_run_once_reconciles_stale_runs_at_the_start_automatically(conn, tmp_path):
    CernConnector.ensure_source(conn)
    conn.execute("INSERT INTO run_ledger (source_id, started_at) VALUES ('cern', %s)", (NOW - timedelta(hours=3),))
    scheduler.run_once(conn, mock_client(), LocalFsStore(tmp_path), "unused", now=NOW, skip_fx=True)
    statuses = conn.execute("SELECT status FROM run_ledger WHERE source_id='cern' ORDER BY run_id").fetchall()
    assert statuses[0][0] == "error"                                 # the stale one, reconciled
    assert statuses[1][0] == "ok"                                    # this run's own real attempt


# --------------------------------------------------------------------- F5: R1 blocking at the ledger level -----------
def test_three_consecutive_failed_runs_mark_the_third_as_blocked_not_plain_error(conn, tmp_path):
    """crawl_state.py's own R1 counter (3 consecutive failed runs -> blocked), already used per-URL inside
    board.py, reused here at the SOURCE level via a distinct key (__scheduler__) so the two never collide.
    Uses cern (a real, recognised bespoke source) so build_connector() actually attempts a fetch - an
    unrecognised source_id would just get a trivially-succeeding NoOp, never exercising this path at all."""
    CernConnector.ensure_source(conn)
    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(500))),
        rps=1000, sleep=lambda s: None, max_retries=0)
    store = LocalFsStore(tmp_path)
    statuses = []
    for i in range(3):
        result = scheduler.run_once(conn, client, store, "unused", now=NOW + timedelta(hours=27 * i), skip_fx=True)  # +27h steps (see the earlier test's own comment): clears R4's +/-10% jitter window regardless of date
        statuses.append(result["results"][0]["status"])
    assert statuses == ["error", "error", "blocked"]                  # the 3rd consecutive failure blocks
    note = conn.execute("SELECT note FROM run_ledger WHERE source_id='cern' ORDER BY run_id DESC LIMIT 1").fetchone()[0]
    assert "unreachable_after_3_runs" in note


def test_a_success_after_failures_clears_the_r1_counter(conn, tmp_path):
    """Uses cern (a real, recognised bespoke source id) - build_connector() dispatches on source_row["id"],
    so a made-up id here would never reach CernConnector at all."""
    CernConnector.ensure_source(conn)
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(500)
        if req.url.path == "/jobs/feed/":
            return httpx.Response(200, content=RSS.encode())
        if req.url.path == "/jobs/postdoc-fellow/":
            return httpx.Response(200, content=DETAIL.encode())
        return httpx.Response(404)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000,
                          sleep=lambda s: None, max_retries=0)
    store = LocalFsStore(tmp_path)
    statuses = []
    for i in range(3):
        result = scheduler.run_once(conn, client, store, "unused", now=NOW + timedelta(hours=27 * i), skip_fx=True)  # +27h steps (see the earlier test's own comment): clears R4's +/-10% jitter window regardless of date
        statuses.append(result["results"][0]["status"])
    assert statuses == ["error", "error", "ok"]                       # recovered before the 3rd (blocking) failure

    # the R1 counter itself is cleared by the success, not just reflected in this run's own status - a
    # SUBSEQUENT failure must start counting from zero again, not continue toward blocking from 2.
    state = conn.execute(
        "SELECT consecutive_failures, blocked_reason FROM crawl_state WHERE source_id='cern' AND key='__scheduler__'"
    ).fetchone()
    assert state == (0, None)
