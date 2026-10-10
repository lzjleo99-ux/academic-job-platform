"""T32 (packet 019): the small labelled [SAMPLE] demo fixture must never load into real/production data by
default - only tools/dev_up.py's own explicit demo=True call (its dev stack, which the Playwright
flows.spec.ts suite depends on) still gets it automatically. `ajp-init`/production bootstrap now default to
OFF, opt-in via --with-demo or AJP_LOAD_DEMO=1."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ajp import bootstrap
from ajp.rawstore import LocalFsStore


def _sample_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM listing WHERE title_raw LIKE '[SAMPLE]%'").fetchone()[0]


def test_bootstrap_all_default_loads_zero_sample_rows(conn, tmp_path):
    """The real fix: bootstrap_all's own default flipped from demo=True to demo=False."""
    counts = bootstrap.bootstrap_all(conn, LocalFsStore(tmp_path))
    assert counts["demo_listings"] == 0
    assert _sample_count(conn) == 0


def test_bootstrap_all_demo_true_still_works_exactly_as_before(conn, tmp_path):
    """tools/dev_up.py's own explicit demo=True call (and the Playwright flows.spec.ts suite depending on
    those fixture rows) must never regress - only the DEFAULT changed, never this explicit path."""
    counts = bootstrap.bootstrap_all(conn, LocalFsStore(tmp_path), demo=True)
    assert counts["demo_listings"] > 0
    # not necessarily equal to counts["demo_listings"] - the fixture deliberately includes a cross-source-
    # dedupe demo row (see test_bootstrap_demo_listings.py's own docstring), so N processed items can
    # collapse into fewer distinct stored rows.
    assert _sample_count(conn) > 0


@pytest.mark.slow
def test_ajp_init_cli_with_a_fresh_temp_data_dir_loads_zero_sample_rows_by_default():
    """PM's own explicit T32 requirement: 'a fresh init without the flag must load zero [SAMPLE] rows
    (assert in a test using a temp data dir)' - a real, genuinely fresh `ajp-init` run (its own embedded
    Postgres, its own migrations, its own bootstrap), not just the bootstrap_all() unit tests above."""
    with tempfile.TemporaryDirectory(prefix="ajp-init-test-") as data_dir:
        env = {**os.environ, "AJP_DATA_DIR": data_dir}
        env.pop("AJP_LOAD_DEMO", None)
        result = subprocess.run(
            [sys.executable, "-m", "ajp.cli.init", "--data-dir", data_dir],
            cwd=str(Path(__file__).parents[1] / "src"), env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        assert "0 demo listings" in result.stdout

        import pgserver
        import psycopg
        server = pgserver.get_server(str(Path(data_dir) / "pgdata"))
        with psycopg.connect(server.get_uri()) as conn:
            n = conn.execute("SELECT count(*) FROM listing WHERE title_raw LIKE '[SAMPLE]%'").fetchone()[0]
        assert n == 0


@pytest.mark.slow
def test_ajp_init_cli_with_with_demo_flag_loads_the_fixture():
    with tempfile.TemporaryDirectory(prefix="ajp-init-test-") as data_dir:
        env = {**os.environ, "AJP_DATA_DIR": data_dir}
        env.pop("AJP_LOAD_DEMO", None)
        result = subprocess.run(
            [sys.executable, "-m", "ajp.cli.init", "--data-dir", data_dir, "--with-demo"],
            cwd=str(Path(__file__).parents[1] / "src"), env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        assert "0 demo listings" not in result.stdout

        import pgserver
        import psycopg
        server = pgserver.get_server(str(Path(data_dir) / "pgdata"))
        with psycopg.connect(server.get_uri()) as conn:
            n = conn.execute("SELECT count(*) FROM listing WHERE title_raw LIKE '[SAMPLE]%'").fetchone()[0]
        assert n > 0


@pytest.mark.slow
def test_ajp_init_cli_respects_the_ajp_load_demo_env_var():
    with tempfile.TemporaryDirectory(prefix="ajp-init-test-") as data_dir:
        env = {**os.environ, "AJP_DATA_DIR": data_dir, "AJP_LOAD_DEMO": "1"}
        result = subprocess.run(
            [sys.executable, "-m", "ajp.cli.init", "--data-dir", data_dir],
            cwd=str(Path(__file__).parents[1] / "src"), env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        assert "0 demo listings" not in result.stdout
