import shutil

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
