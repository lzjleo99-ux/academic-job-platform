"""T20 acceptance: the discovered -> dry_run -> active state machine (0017_discovery_state) - never
auto-promoted to active without schema-valid fixture rows as evidence."""
import psycopg
from psycopg.rows import dict_row

from ajp import discovery


def _seed_org(conn, slug="test-org"):
    conn.execute("""INSERT INTO org_master (slug, name_en, country, type, homepage)
                    VALUES (%s, 'Test Org', 'RS', 'faculty', 'https://test-org.invalid/')""", (slug,))


def test_record_discovery_writes_found_urls_as_discovered_rows(conn):
    _seed_org(conn)
    result = discovery.DiscoveryResult("test-org", "https://test-org.invalid/", "href_harvest",
                                       found_urls=["https://test-org.invalid/konkursi"], paths_fetched=1)
    n = discovery.record_discovery(conn, result)
    assert n == 1
    row = conn.execute("SELECT state, method FROM discovery_state WHERE org_slug='test-org'").fetchone()
    assert row == ("discovered", "href_harvest")


def test_record_discovery_is_idempotent_and_never_resets_an_already_promoted_row(conn):
    _seed_org(conn)
    result = discovery.DiscoveryResult("test-org", "https://test-org.invalid/", "href_harvest",
                                       found_urls=["https://test-org.invalid/konkursi"], paths_fetched=1)
    discovery.record_discovery(conn, result)
    discovery.promote(conn, "test-org", "https://test-org.invalid/konkursi", "dry_run", promoted_by="test",
                      dry_run_rows=3)
    discovery.record_discovery(conn, result)                            # a re-run "discovering" the same url again
    row = conn.execute("SELECT state, dry_run_rows FROM discovery_state WHERE org_slug='test-org'").fetchone()
    assert row == ("dry_run", 3)                                        # NOT reset back to 'discovered'


def test_promote_to_active_requires_dry_run_rows_as_evidence(conn):
    _seed_org(conn)
    result = discovery.DiscoveryResult("test-org", "https://test-org.invalid/", "href_harvest",
                                       found_urls=["https://test-org.invalid/konkursi"], paths_fetched=1)
    discovery.record_discovery(conn, result)
    ok = discovery.promote(conn, "test-org", "https://test-org.invalid/konkursi", "active", promoted_by="test")
    assert ok is False                                                  # no dry_run_rows evidence yet - refused
    row = conn.execute("SELECT state FROM discovery_state WHERE org_slug='test-org'").fetchone()
    assert row == ("discovered",)                                       # unchanged


def test_promote_to_active_succeeds_once_dry_run_rows_is_set(conn):
    _seed_org(conn)
    result = discovery.DiscoveryResult("test-org", "https://test-org.invalid/", "href_harvest",
                                       found_urls=["https://test-org.invalid/konkursi"], paths_fetched=1)
    discovery.record_discovery(conn, result)
    discovery.promote(conn, "test-org", "https://test-org.invalid/konkursi", "dry_run", promoted_by="test",
                      dry_run_rows=5, ats_family="gen_wordpress")
    ok = discovery.promote(conn, "test-org", "https://test-org.invalid/konkursi", "active", promoted_by="test")
    assert ok is True
    row = conn.execute("SELECT state, ats_family FROM discovery_state WHERE org_slug='test-org'").fetchone()
    assert row == ("active", "gen_wordpress")


def test_promote_on_a_nonexistent_row_returns_false_without_raising(conn):
    _seed_org(conn)
    ok = discovery.promote(conn, "test-org", "https://test-org.invalid/never-discovered", "dry_run",
                           promoted_by="test", dry_run_rows=1)
    assert ok is False


def test_record_run_logs_every_attempt_hit_or_not(conn):
    hit = discovery.DiscoveryResult("a", "https://a.invalid/", "href_harvest", ["https://a.invalid/x"], 1)
    miss = discovery.DiscoveryResult("b", "https://b.invalid/", "none", [], 12)
    unreachable = discovery.DiscoveryResult("c", "https://c.invalid/", "homepage_unreachable", [], 0,
                                            error="robots denied")
    for r in (hit, miss, unreachable):
        discovery.record_run(conn, r)
    rows = conn.execute("SELECT org_slug, method, paths_fetched, found_count, error FROM discovery_run_log "
                        "ORDER BY org_slug").fetchall()
    assert rows == [
        ("a", "href_harvest", 1, 1, None),
        ("b", "none", 12, 0, None),
        ("c", "homepage_unreachable", 0, 0, "robots denied"),
    ]


def test_governance_snapshot_aggregates_funnel_and_per_host_activity(conn):
    _seed_org(conn, "a"); _seed_org(conn, "b")
    discovery.record_run(conn, discovery.DiscoveryResult("a", "https://a.invalid/", "href_harvest",
                                                          ["https://a.invalid/x"], 1))
    discovery.record_run(conn, discovery.DiscoveryResult("b", "https://b.invalid/", "homepage_unreachable", [], 0,
                                                          error="robots denied: unavailable"))
    discovery.record_discovery(conn, discovery.DiscoveryResult("a", "https://a.invalid/", "href_harvest",
                                                                ["https://a.invalid/x"], 1))
    snap = discovery.governance_snapshot(conn)
    assert snap["discovery_funnel"] == {"discovered": 1, "dry_run": 0, "active": 0}
    hosts = {h["host"]: h for h in snap["per_host_last_24h"]}
    assert hosts["a.invalid"]["runs_24h"] == 1 and hosts["a.invalid"]["paths_fetched_24h"] == 1
    assert hosts["b.invalid"]["denied_24h"] == 1
    assert snap["robots_denied_24h_total"] == 1


def test_governance_and_coverage_work_identically_over_a_dict_row_connection(dsn):
    """The API layer's own get_conn() sets dict_row by default (unlike this test file's plain `conn`
    fixture, tuple by default) - governance_snapshot/coverage_report/promote must behave identically
    either way, same fix T18's registry.py needed for the same reason."""
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as dict_conn:
        _seed_org(dict_conn, "dict-org")
        discovery.record_run(dict_conn, discovery.DiscoveryResult("dict-org", "https://dict-org.invalid/",
                                                                   "href_harvest", ["https://dict-org.invalid/x"], 1))
        discovery.record_discovery(dict_conn, discovery.DiscoveryResult("dict-org", "https://dict-org.invalid/",
                                                                        "href_harvest", ["https://dict-org.invalid/x"], 1))
        snap = discovery.governance_snapshot(dict_conn)                 # must not raise / must return real counts
        assert snap["discovery_funnel"]["discovered"] == 1
        report = discovery.coverage_report(dict_conn)
        assert report["discovery_runs_total"] == 1
        ok = discovery.promote(dict_conn, "dict-org", "https://dict-org.invalid/x", "dry_run",
                               promoted_by="test", dry_run_rows=2)
        assert ok is True
        refused = discovery.promote(dict_conn, "dict-org", "https://dict-org.invalid/nonexistent", "dry_run",
                                    promoted_by="test", dry_run_rows=2)
        assert refused is False
        # promoting to 'active' WITHOUT passing dry_run_rows exercises the tuple_row-cursor lookback fix -
        # this must read the already-set dry_run_rows=2 from the row itself, not raise on a dict row.
        activated = discovery.promote(dict_conn, "dict-org", "https://dict-org.invalid/x", "active",
                                      promoted_by="test")
        assert activated is True


def test_coverage_report_uses_real_queried_counts_only(conn):
    _seed_org(conn, "a")
    conn.execute("UPDATE org_master SET career_urls = ARRAY['https://a.invalid/konkursi'] WHERE slug='a'")
    _seed_org(conn, "b")
    discovery.record_run(conn, discovery.DiscoveryResult("a", "https://a.invalid/", "href_harvest",
                                                          ["https://a.invalid/x"], 1))
    report = discovery.coverage_report(conn)
    assert report["rs_orgs_total"] == 2
    assert report["rs_orgs_with_career_urls"] == 1
    assert report["discovery_runs_total"] == 1
    assert report["discovery_by_method"]["href_harvest"]["found"] == 1
