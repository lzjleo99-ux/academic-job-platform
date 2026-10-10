"""T23: a real 10,000-row synthetic-listing performance benchmark against a real embedded Postgres (pgserver,
the same engine ajp-init uses) - not mocked, not estimated. Prints machine spec (for an honest, reproducible
report - these numbers are only meaningful next to the machine they were measured on), EXPLAIN plans for the
indexed filters, and p95/max latency for the representative API endpoints packet 014 named.

    python tools/perf_10k.py

tests/test_performance_10k.py runs the same seed + a subset of these assertions automatically as part of the
offline suite (a smaller, CI-safe check); this script is the fuller, human-readable report to paste into an
events.jsonl entry.
"""
from __future__ import annotations

import platform
import shutil
import statistics
import tempfile
import time

import pgserver
import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.migrate import apply_migrations

N_ROWS = 10_000

# One SQL statement, generate_series-driven - fast (a few hundred ms for 10k rows) and deterministic; every
# column exercised by a real filter/facet/sort gets real variety (10 countries + 10% null-country, 10
# position_kinds, 4 kinds, 4 status values, a salary on 1/4 of rows, deadlines/posted_at spread over a year).
SEED_SQL = """
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
FROM generate_series(1, {n}) AS i
"""

EXPLAIN_QUERIES = [
    ("a single country, status=open (listing_country_status_idx)",
     "SELECT count(*) FROM listing WHERE country_iso2 = 'RS' AND status = 'open'"),
    ("a single position_kind (listing_position_kind_idx)",
     "SELECT count(*) FROM listing WHERE position_kind = 'phd'"),
    ("a single location_class (listing_location_class_idx, T23)",
     "SELECT count(*) FROM listing WHERE location_class = 'remote'"),
    ("open listings closing within 14 days (listing_open_deadline_idx, the real deadline_bucket=closing_soon shape)",
     "SELECT count(*) FROM listing WHERE status = 'open' AND deadline IS NOT NULL "
     "AND deadline <= now() + interval '14 days'"),
]


def main() -> None:
    print(f"machine: {platform.platform()} / {platform.processor() or platform.machine()} "
          f"/ python {platform.python_version()}")
    d = tempfile.mkdtemp(prefix="ajp-perf-")
    server = pgserver.get_server(d)
    try:
        dsn = server.get_uri()
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("CREATE DATABASE ajp_perf")
        full_dsn = make_conninfo(dsn, dbname="ajp_perf")
        apply_migrations(full_dsn)
        conn = psycopg.connect(full_dsn, autocommit=True, row_factory=dict_row)
        conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES "
                     "('perf_src','scrape_html','https://x.invalid',true)")

        t0 = time.perf_counter()
        conn.execute(SEED_SQL.format(n=N_ROWS))
        insert_s = time.perf_counter() - t0
        conn.execute("ANALYZE listing")
        print(f"\nseeded {N_ROWS} rows in {insert_s:.2f}s")

        print("\n--- EXPLAIN (index usage) -----------------------------------------------")
        for label, sql in EXPLAIN_QUERIES:
            plan = "\n  ".join(r["QUERY PLAN"] for r in conn.execute("EXPLAIN " + sql).fetchall())
            uses_index = "Index" in plan or "Bitmap" in plan
            print(f"[{'INDEX' if uses_index else 'SEQ SCAN'}] {label}\n  {plan}")

        app = create_app()

        def _override():
            yield conn

        app.dependency_overrides[get_conn] = _override
        client = TestClient(app)

        print("\n--- p95 latency (20 requests each, real embedded pg, real HTTP layer) ----")
        checks = [
            ("/v1/listings (default view)", "/v1/listings", None),
            ("/v1/listings?country=RS", "/v1/listings", {"country": "RS"}),
            ("/v1/listings?position_kind=phd", "/v1/listings", {"position_kind": "phd"}),
            ("/v1/listings?page_size=100", "/v1/listings", {"page_size": 100}),
            ("/v1/listings?location_class=remote", "/v1/listings", {"location_class": "remote"}),
            ("/v1/facets", "/v1/facets", None),
        ]
        some_id = conn.execute("SELECT id FROM listing LIMIT 1").fetchone()["id"]
        checks.append(("/v1/listings/{id}", f"/v1/listings/{some_id}", None))

        all_under_200ms = True
        for label, path, params in checks:
            times = []
            for _ in range(20):
                t = time.perf_counter()
                r = client.get(path, params=params)
                times.append((time.perf_counter() - t) * 1000)
                assert r.status_code == 200, r.text
            times.sort()
            p95 = times[int(len(times) * 0.95) - 1]
            ok = p95 < 200
            all_under_200ms &= ok
            print(f"[{'OK' if ok else 'SLOW'}] {label}: p95={p95:.1f}ms max={max(times):.1f}ms min={min(times):.1f}ms")

        print(f"\nAll endpoints under p95<200ms: {all_under_200ms}")
    finally:
        server.cleanup()
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
