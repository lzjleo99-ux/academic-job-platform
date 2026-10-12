import shutil
from datetime import datetime, timezone

import psycopg
import pytest

from ajp.migrate import MIGRATIONS_DIR, MigrationError, apply_migrations, discover

TABLES = {"source", "org_master", "listing", "fx_rate", "schema_migrations"}


def _tables(dsn):
    with psycopg.connect(dsn) as c:
        return {r[0] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}


def test_migrate_on_empty_db(empty_dsn):
    applied = apply_migrations(empty_dsn)
    assert applied == [p.name for p in discover()]
    assert TABLES <= _tables(empty_dsn)


def test_rerun_is_noop(dsn):
    assert apply_migrations(dsn) == []


def test_edited_migration_is_rejected(empty_dsn, tmp_path):
    shutil.copytree(MIGRATIONS_DIR, tmp_path / "m")
    apply_migrations(empty_dsn, tmp_path / "m")
    f = tmp_path / "m" / "0002_source_org_master.sql"
    f.write_text(f.read_text() + "\n-- tampered\n")
    with pytest.raises(MigrationError, match="modified after being applied"):
        apply_migrations(empty_dsn, tmp_path / "m")


def test_failed_migration_rolls_back(empty_dsn, tmp_path):
    shutil.copytree(MIGRATIONS_DIR, tmp_path / "m")
    (tmp_path / "m" / "0099_broken.sql").write_text("CREATE TABLE t1 (a int); SELECT 1/0;")
    with pytest.raises(psycopg.errors.DivisionByZero):
        apply_migrations(empty_dsn, tmp_path / "m")
    assert "t1" not in _tables(empty_dsn)
    with psycopg.connect(empty_dsn) as c:
        assert c.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(discover())  # broken one not recorded


def test_trgm_indexes_follow_extension(conn):
    has_ext = conn.execute("SELECT count(*) FROM pg_extension WHERE extname='pg_trgm'").fetchone()[0]
    has_idx = conn.execute("SELECT count(*) FROM pg_indexes WHERE indexname='listing_title_trgm_idx'").fetchone()[0]
    assert has_ext == has_idx


def test_backfill_repairs_rows_left_at_empty_sources(conn):
    """F14's data half. The code fix (crawl_orgs -> upsert_listing_deduped, plus the CASE/EXISTS append
    shape) stops NEW rows from being written with sources='[]', but the rows already persisted by the old
    path stay invisible to the API's per-source join until something repairs them - verified live in the
    deployed database: 935 of them, all rs_org_boards, every first_seen before the fix shipped. Migration
    0025 does that once, reconstructing the entry from the row's OWN columns rather than inventing a 'now'
    that would claim the ad was seen again today. Its shape is the one upsert_listing_deduped writes."""
    sql = (MIGRATIONS_DIR / "0025_backfill_listing_sources.sql").read_text(encoding="utf-8")
    for sid in ("rs_org_boards", "nature_careers"):
        conn.execute("INSERT INTO source (id, kind, base_url) VALUES (%s,'feed','https://x.invalid')", (sid,))
    conn.execute(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, position_kind,
                               sources, first_seen, last_seen)
           VALUES ('legacy-1','rs_org_boards','https://x.invalid/a','https://x.invalid/a','Legacy ad','phd',
                   '[]'::jsonb, %s, %s)""",
        (datetime(2026, 9, 25, 2, tzinfo=timezone.utc), datetime(2026, 9, 25, 2, tzinfo=timezone.utc)),
    )

    conn.execute(sql)
    sources = conn.execute("SELECT sources FROM listing WHERE ext_uid='legacy-1'").fetchone()[0]
    assert len(sources) == 1
    entry = sources[0]
    assert set(entry) == {"source", "url", "first_seen", "last_seen"}
    assert entry["source"] == "rs_org_boards"
    assert entry["url"] == "https://x.invalid/a"
    # stored as an ISO-8601 STRING, like every healthy entry - never a bare epoch or an object
    assert isinstance(entry["first_seen"], str) and "T" in entry["first_seen"]
    assert isinstance(entry["last_seen"], str) and "T" in entry["last_seen"]
    # ...and it round-trips back to the row's own instants (compared as timestamptz, so the assertion is
    # independent of the session's own timezone, which only affects how the string is RENDERED)
    first_seen, last_seen = conn.execute(
        """SELECT (sources->0->>'first_seen')::timestamptz, (sources->0->>'last_seen')::timestamptz
           FROM listing WHERE ext_uid='legacy-1'"""
    ).fetchone()
    assert first_seen == datetime(2026, 9, 25, 2, tzinfo=timezone.utc)
    assert last_seen == datetime(2026, 9, 25, 2, tzinfo=timezone.utc)

    # idempotent: a re-run appends nothing (the WHERE is what makes it safe on a fresh install, too)
    conn.execute(sql)
    assert conn.execute(
        "SELECT jsonb_array_length(sources) FROM listing WHERE ext_uid='legacy-1'"
    ).fetchone()[0] == 1

    # and an already-healthy multi-source row is left strictly alone
    conn.execute(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, position_kind)
           VALUES ('healthy-1','nature_careers','https://x.invalid/h','https://x.invalid/h','Healthy ad','phd')"""
    )
    conn.execute(
        """UPDATE listing SET sources = '[{"source":"nature_careers","url":"https://x.invalid/h",
              "first_seen":"2026-09-25T02:00:00+00:00","last_seen":"2026-09-25T02:00:00+00:00"}]'::jsonb
           WHERE ext_uid='healthy-1'"""
    )
    conn.execute(sql)
    sources = conn.execute("SELECT sources FROM listing WHERE ext_uid='healthy-1'").fetchone()[0]
    assert [s["source"] for s in sources] == ["nature_careers"]
