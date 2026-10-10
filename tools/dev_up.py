"""One-command dev stack: embedded Postgres (persisted, not the tests' throwaway one) + migrations + org/channel
seed + a small synthetic listings fixture (offline, no network) + the FastAPI dev server.

    python tools/dev_up.py                      # http://127.0.0.1:8000, localhost-only

Re-running is idempotent: migrations are forward-only/checksummed, seeds use ON CONFLICT upserts, and the
listings fixture is re-ingested by its own stable ext_uid (a re-run refreshes rows, never duplicates them).
All the actual seeding/bootstrap work lives in ajp.bootstrap (shared with the packaged `ajp-init` console
script, T21) - this script is now just its CLI wrapper plus starting uvicorn.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import psycopg
import uvicorn

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ajp import bootstrap  # noqa: E402
from ajp.migrate import apply_migrations  # noqa: E402
from ajp.rawstore import LocalFsStore  # noqa: E402

DATA_DIR = ROOT / "data"
# NOT under DATA_DIR: embedded postgres's own pg_ctl/postgres binaries reject a -D path containing non-ASCII
# bytes (found live, 2026-09-23 - this checkout's own path has one: ".../PhD .../phd.../..."; pg_ctl fails with
# `postgres: invalid argument` and no clearer diagnostic). tests/conftest.py never hits this because it uses
# tempfile.mkdtemp(), which resolves under the OS temp dir - always ASCII on every platform this targets.
# Same fix here, but persisted (not cleaned up) across dev runs, with a --pgdata override for anyone whose own
# temp dir is itself non-ASCII.
DEFAULT_PGDATA = Path(tempfile.gettempdir()) / "ajp-dev-pgdata"
RAWSTORE = DATA_DIR / "raw"          # plain Python file I/O - unaffected, kept inside the repo for discoverability


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")   # localhost-only default (packet 007 dev_runbook)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--pgdata", default=str(DEFAULT_PGDATA), help="must be an ASCII-only path - see comment above")
    ap.add_argument("--no-serve", action="store_true", help="seed only, do not start uvicorn (used by smoke tests)")
    args = ap.parse_args()

    pgdata = Path(args.pgdata)
    pgdata.mkdir(parents=True, exist_ok=True)
    RAWSTORE.mkdir(parents=True, exist_ok=True)
    import pgserver
    server = pgserver.get_server(str(pgdata))
    dsn = server.get_uri()
    try:
        apply_migrations(dsn)
        with psycopg.connect(dsn, autocommit=True) as conn:
            counts = bootstrap.bootstrap_all(conn, LocalFsStore(RAWSTORE), demo=True)
            print(f"seeded: {counts['orgs_base']} base + {counts['orgs_supplement']} supplement = "
                 f"{counts['orgs_total']} orgs, {counts['demo_listings']} sample listings, dsn={dsn}")
        if args.no_serve:
            return 0
        os.environ["AJP_DSN"] = dsn
        print(f"http://{args.host}:{args.port}/docs")
        uvicorn.run("ajp.api.app:app", host=args.host, port=args.port, reload=False)
        return 0
    finally:
        if args.no_serve:
            server.cleanup()


if __name__ == "__main__":
    sys.exit(main())
