"""T18 acceptance: 'all new sources appear in /v1/sources and sources.json inventory with route type'."""
import json

from ajp.registry import fetch_source_statuses, write_sources_json


def test_fetch_source_statuses_surfaces_ats_and_tdm_fields(conn):
    conn.execute(
        """INSERT INTO source (id, kind, base_url, robots_ok, ats_family, ats_tenant, ats_site,
                               tdm_reservation, geo_scope)
           VALUES ('ats_workday_cxs__embl_embl', 'api', 'https://embl.wd103.myworkdayjobs.com/EMBL', true,
                   'ats_workday_cxs', 'embl', 'EMBL', 'none', 'INTL')"""
    )
    conn.execute(
        """INSERT INTO source (id, kind, base_url, robots_ok, tdm_reservation)
           VALUES ('academics_com', 'channel', 'https://www.academics.com/', false, 'declared')"""
    )
    rows = {r["id"]: r for r in fetch_source_statuses(conn)}
    workday = rows["ats_workday_cxs__embl_embl"]
    assert workday["ats_family"] == "ats_workday_cxs" and workday["ats_tenant"] == "embl"
    assert workday["ats_site"] == "EMBL" and workday["tdm_reservation"] == "none"
    assert workday["last_run_status"] == "never_run"                  # no crawl_state row yet
    academics = rows["academics_com"]
    assert academics["tdm_reservation"] == "declared"
    assert academics["last_run_status"] == "not_applicable"           # channel source, never fetched


def test_write_sources_json_produces_a_real_file(conn, tmp_path):
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('x', 'feed', 'https://x.invalid', true)")
    path = write_sources_json(conn, tmp_path / "sources.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert any(d["id"] == "x" for d in data)


# ---------------------------------------------------------------- T24 compliance dashboard -------------------
def test_requests_24h_sums_pages_fetched_from_run_ledger_within_the_window(conn):
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('cs1', 'feed', 'https://x.invalid', true)")
    conn.execute(
        """INSERT INTO run_ledger (source_id, started_at, finished_at, status, pages_fetched)
           VALUES ('cs1', now() - interval '1 hour', now(), 'ok', 12),
                  ('cs1', now() - interval '20 hours', now(), 'ok', 8),
                  ('cs1', now() - interval '2 days', now(), 'ok', 100)"""            # outside the 24h window
    )
    row = next(r for r in fetch_source_statuses(conn) if r["id"] == "cs1")
    assert row["requests_24h"] == 20                # 12 + 8, the 2-day-old run excluded
    assert row["denied_24h"] == 0


def test_denied_24h_counts_a_real_robots_denied_or_manifest_violation_note(conn):
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('cs2', 'feed', 'https://x.invalid', true)")
    conn.execute(
        """INSERT INTO run_ledger (source_id, started_at, finished_at, status, note)
           VALUES ('cs2', now() - interval '1 hour', now(), 'error', 'RobotsDenied: disallowed'),
                  ('cs2', now() - interval '2 hours', now(), 'error', 'ManifestViolation: URL not in manifest'),
                  ('cs2', now() - interval '3 hours', now(), 'error', 'FetchFailed: timeout')"""
    )
    row = next(r for r in fetch_source_statuses(conn) if r["id"] == "cs2")
    assert row["denied_24h"] == 2                   # the FetchFailed row never counted as a denial


def test_a_source_with_no_recent_runs_reports_zero_not_null(conn):
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('cs3', 'feed', 'https://x.invalid', true)")
    row = next(r for r in fetch_source_statuses(conn) if r["id"] == "cs3")
    assert row["requests_24h"] == 0 and row["denied_24h"] == 0
