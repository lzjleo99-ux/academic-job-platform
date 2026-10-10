import itertools
import shutil
import tempfile
from pathlib import Path

import pgserver
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from ajp.migrate import apply_migrations

FIXTURES = Path(__file__).parent / "fixtures"
_counter = itertools.count()


@pytest.fixture(scope="session")
def pg_admin_dsn():
    d = tempfile.mkdtemp(prefix="ajp-pg-")
    server = pgserver.get_server(d)
    yield server.get_uri()
    server.cleanup()
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def empty_dsn(pg_admin_dsn):
    """A brand-new, empty database (no migrations applied)."""
    name = f"ajp_test_{next(_counter)}"
    with psycopg.connect(pg_admin_dsn, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    return make_conninfo(pg_admin_dsn, dbname=name)


@pytest.fixture()
def dsn(empty_dsn):
    apply_migrations(empty_dsn)
    return empty_dsn


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        yield c
