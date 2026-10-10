"""T23: N+1 audit on /v1/listings, /v1/listings/{id} and /v1/facets - asserts the number of DB queries a
request issues stays CONSTANT as the row count grows (the actual definition of "no N+1": a query count that
scales with the number of rows returned, not the request itself). None of these routes were found to
actually have an N+1 (rows.py builds every card from one already-joined SELECT; facets.py issues one query
PER FACET DIMENSION, a small fixed number, never one per row) - this is a permanent regression guard against
that ever regressing, not a fix for a bug that existed.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from psycopg.rows import tuple_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.ingest import upsert_listing_deduped
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime.now(timezone.utc)


class QueryCounter:
    """Wraps one connection's own .execute for the lifetime of the context manager, counting every call -
    real query counting against the real embedded Postgres, never estimated or read from an EXPLAIN plan."""

    def __init__(self, conn):
        self.conn = conn
        self.count = 0
        self._orig = conn.execute

    def __enter__(self):
        def counted(*args, **kwargs):
            self.count += 1
            return self._orig(*args, **kwargs)

        self.conn.execute = counted
        return self

    def __exit__(self, *exc):
        self.conn.execute = self._orig


def _add_source(conn, sid):
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES (%s,'scrape_html','https://x.invalid',true) "
                 "ON CONFLICT DO NOTHING", (sid,))


def _raw(store, sid, n=[0]):
    n[0] += 1
    return put_raw(store, kind="html", source_id=sid, url=f"https://x.invalid/{sid}/{n[0]}", content=b"x",
                   fetched_at=NOW, status=200, robots_allowed=True).key


def _seed_n_rows(conn, store, n: int, org_id=None, offset: int = 0):
    # upsert_listing() reads its own tuple-shaped rows positionally - force tuple_row for the duration of
    # seeding regardless of whatever row_factory the API client (below) may have already switched conn to.
    saved = conn.row_factory
    conn.row_factory = tuple_row
    try:
        for i in range(offset, offset + n):        # a distinct ext_uid range per call - never re-upserts
            row = {                                 # (and silently merges into) a row an earlier call made
                "ext_uid": f"row-{i}", "source_id": "n1_src", "url": f"https://x.invalid/job/{i}",
                "title_raw": f"Researcher {i}", "country_iso2": "RS", "position_kind": "researcher_title",
                "kind": "employment", "org_id": org_id, "org_raw": "Demo Org", "location_class": "located",
                "raw_html_ref": _raw(store, "n1_src"),
            }
            upsert_listing_deduped(conn, row, store)
    finally:
        conn.row_factory = saved


def _client(conn):
    app = create_app()
    conn.row_factory = dict_row

    def _override_get_conn():
        yield conn

    app.dependency_overrides[get_conn] = _override_get_conn
    return TestClient(app)


@pytest.fixture()
def store(tmp_path):
    return LocalFsStore(tmp_path)


def test_listings_query_count_does_not_scale_with_row_count(conn, store):
    _add_source(conn, "n1_src")
    org_id = conn.execute(
        "INSERT INTO org_master (slug, name_en, country, type) VALUES ('n1-org','N1 Org','RS','university') RETURNING id"
    ).fetchone()[0]

    _seed_n_rows(conn, store, 3, org_id=org_id)
    client = _client(conn)     # switches conn.row_factory to dict_row - must come AFTER seeding (upsert_listing
                                # reads its own tuple-shaped rows positionally)
    with QueryCounter(conn) as qc:
        r = client.get("/v1/listings", params={"kind": "employment", "page_size": 50})
    assert r.status_code == 200 and len(r.json()["results"]) == 3
    small_count = qc.count

    _seed_n_rows(conn, store, 30, org_id=org_id, offset=3)   # 33 rows total now
    with QueryCounter(conn) as qc:
        r = client.get("/v1/listings", params={"kind": "employment", "page_size": 50})
    assert r.status_code == 200 and len(r.json()["results"]) == 33
    large_count = qc.count

    assert small_count == large_count == 2          # exactly: one count(*) + one page SELECT, never per-row


def test_listing_detail_is_always_exactly_one_query(conn, store):
    _add_source(conn, "n1_src")
    _seed_n_rows(conn, store, 1)
    client = _client(conn)
    listing_id = conn.execute("SELECT id FROM listing LIMIT 1").fetchone()["id"]

    with QueryCounter(conn) as qc:
        r = client.get(f"/v1/listings/{listing_id}")
    assert r.status_code == 200
    assert qc.count == 1


def test_facets_query_count_does_not_scale_with_row_count(conn, store):
    _add_source(conn, "n1_src")
    _seed_n_rows(conn, store, 3)
    client = _client(conn)

    with QueryCounter(conn) as qc:
        r = client.get("/v1/facets", params={"kind": "employment"})
    assert r.status_code == 200
    small_count = qc.count

    _seed_n_rows(conn, store, 30, offset=3)
    with QueryCounter(conn) as qc:
        r = client.get("/v1/facets", params={"kind": "employment"})
    assert r.status_code == 200
    large_count = qc.count

    assert small_count == large_count            # one query PER FACET DIMENSION, never per row
    assert small_count <= 15                     # a small, fixed number (13 dimensions as of T23), not a magic exact one
