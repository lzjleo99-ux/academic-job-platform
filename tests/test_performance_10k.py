"""T23: a real 10,000-row synthetic-listing performance regression, against the same embedded Postgres
(pgserver) every other test uses - not mocked, not estimated. Runs as part of the normal offline suite
(seeding+queries take well under a second) so a genuine index or query regression is caught automatically,
not just when someone remembers to run tools/perf_10k.py by hand. That script is the fuller, human-readable
report (machine spec, every EXPLAIN plan in full) for pasting into an events.jsonl entry; this file is the
permanent, minimal CI check.

pg_trgm-absent guard: already covered by test_migrations.py::test_trgm_indexes_follow_extension (the trigram
indexes are only asserted to exist when pg_trgm itself is - this embedded test server has neither, and that
test passes by construction either way). Salary-vs-FX-snapshot tests (incl. the null-FX degraded case):
tests/test_api_salary_fx_snapshot.py.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn

N_ROWS = 10_000

SEED_SQL = f"""
INSERT INTO listing (
  ext_uid, source_id, url, url_canon, title_raw, country_iso2, city,
  position_kind, kind, ft_pt, foreigner_eligible, status, location_class,
  salary_min, salary_max, salary_currency, salary_period, salary_raw,
  deadline, posted_at
)
SELECT
  i::text, 'perf_src', 'https://x.invalid/job/'||i, 'https://x.invalid/job/'||i,
  'Synthetic Role '||i,
  CASE WHEN i % 10 = 0 THEN NULL ELSE (ARRAY['RS','NL','DE','SE','FR','GB','US','CH','AT','IT'])[1 + (i % 10)] END,
  CASE WHEN i % 10 = 0 THEN NULL ELSE 'City'||(i % 50) END,
  (ARRAY['phd','postdoc','research_assistant','researcher_title','professor','lecturer','technician','admin','intern','other'])[1 + (i % 10)],
  CASE WHEN i % 20 = 0 THEN 'enrollment' WHEN i % 33 = 0 THEN 'internship' WHEN i % 47 = 0 THEN 'grant_call' ELSE 'employment' END,
  (ARRAY['full','part','both','unknown'])[1 + (i % 4)],
  (ARRAY['yes','no','unknown'])[1 + (i % 3)],
  CASE WHEN i % 15 = 0 THEN 'closed' WHEN i % 23 = 0 THEN 'expired' ELSE 'open' END,
  CASE WHEN i % 10 = 0 THEN (ARRAY['remote','hybrid','unknown'])[1 + (i % 3)] ELSE 'located' END,
  CASE WHEN i % 4 = 0 THEN (1000 + (i % 5000)) ELSE NULL END,
  CASE WHEN i % 4 = 0 THEN (2000 + (i % 5000)) ELSE NULL END,
  CASE WHEN i % 4 = 0 THEN 'EUR' ELSE NULL END,
  CASE WHEN i % 4 = 0 THEN 'month' ELSE NULL END,
  CASE WHEN i % 4 = 0 THEN 'synthetic salary [SAMPLE]' ELSE NULL END,
  now() + ((i % 90) || ' days')::interval,
  now() - ((i % 365) || ' days')::interval
FROM generate_series(1, {N_ROWS}) AS i
"""


@pytest.fixture()
def seeded_conn(conn):
    """10k synthetic rows seeded into this test's own fresh embedded-pg database (the standard `conn` fixture
    - a real, isolated database per test, per conftest.py). Seeding this many rows via one generate_series
    INSERT is fast (~0.3s measured), so re-seeding per test here is a deliberate, cheap trade for real
    per-test isolation rather than a shared module-scoped database."""
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES "
                 "('perf_src','scrape_html','https://x.invalid',true) ON CONFLICT DO NOTHING")
    conn.execute(SEED_SQL)
    conn.execute("ANALYZE listing")
    return conn


@pytest.fixture()
def client(seeded_conn):
    app = create_app()
    seeded_conn.row_factory = dict_row

    def _override_get_conn():
        yield seeded_conn

    app.dependency_overrides[get_conn] = _override_get_conn
    with TestClient(app) as c:
        yield c


def _p95_ms(fn, n=20) -> float:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return times[int(len(times) * 0.95) - 1]


def test_seeded_row_count_is_real(seeded_conn):
    assert seeded_conn.execute("SELECT count(*) FROM listing").fetchone()[0] == N_ROWS


@pytest.mark.parametrize("label,sql,must_use_index", [
    ("a single country + status=open", "SELECT count(*) FROM listing WHERE country_iso2 = 'RS' AND status = 'open'", True),
    ("a single position_kind", "SELECT count(*) FROM listing WHERE position_kind = 'phd'", True),
    ("a single location_class (T23, R2)", "SELECT count(*) FROM listing WHERE location_class = 'remote'", True),
    ("open listings closing within 14 days (deadline_bucket=closing_soon's real shape)",
     "SELECT count(*) FROM listing WHERE status = 'open' AND deadline IS NOT NULL "
     "AND deadline <= now() + interval '14 days'", True),
])
def test_indexed_filters_use_a_real_index_not_a_full_table_scan(seeded_conn, label, sql, must_use_index):
    plan = "\n".join(r[0] for r in seeded_conn.execute("EXPLAIN " + sql).fetchall())
    uses_index = "Index" in plan or "Bitmap" in plan
    assert uses_index == must_use_index, f"{label}: expected index-use={must_use_index}, plan was:\n{plan}"


@pytest.mark.parametrize("path,params", [
    ("/v1/listings", None),
    ("/v1/listings", {"country": "RS"}),
    ("/v1/listings", {"position_kind": "phd"}),
    ("/v1/listings", {"page_size": 100}),
    ("/v1/listings", {"location_class": "remote"}),
    ("/v1/facets", None),
])
def test_listing_and_facet_endpoints_stay_under_the_200ms_p95_budget(client, path, params):
    p95 = _p95_ms(lambda: client.get(path, params=params))
    assert p95 < 200, f"{path} {params}: p95={p95:.1f}ms exceeds the 200ms budget"


def test_listing_detail_endpoint_stays_under_the_200ms_p95_budget(seeded_conn, client):
    some_id = seeded_conn.execute("SELECT id FROM listing LIMIT 1").fetchone()["id"]
    p95 = _p95_ms(lambda: client.get(f"/v1/listings/{some_id}"))
    assert p95 < 200
