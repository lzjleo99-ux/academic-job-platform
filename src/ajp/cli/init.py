"""`ajp-init` (T21/T22 console script): embedded Postgres + migrations + ALL seeds (base RS org seed + the
OpenAlex reconciliation supplement, R3: 186 orgs total). Idempotent (safe to re-run) and works from a plain
`pip install .` - no repo checkout required, unlike tools/dev_up.py (which this module's own ajp.bootstrap now
backs too, so the two never drift apart on what "seeded" means).

    ajp-init [--data-dir PATH] [--with-demo]

The small labelled [SAMPLE] demo fixture is OFF by default (T32, packet 019: a real production install must
never load fabricated ads unless explicitly asked) - opt in with --with-demo or AJP_LOAD_DEMO=1.

Prints the DSN on success - `ajp-run`/`ajp-api` (or a shell script chaining them) read AJP_DSN themselves,
but this is the one place that actually creates the database, so its own stdout is the source of truth for
"what DSN did this install end up with".
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

from ajp import bootstrap
from ajp.migrate import apply_migrations
from ajp.rawstore import LocalFsStore

DEFAULT_DATA_DIR = Path(os.environ.get("AJP_DATA_DIR", str(Path.home() / ".ajp")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                    help="holds pgdata/ and raw/ - must be an ASCII-only path (pg_ctl's own requirement)")
    # T32 (packet 019): demo listings are OFF by default now - opt in with --with-demo or AJP_LOAD_DEMO=1.
    # --no-demo is kept accepted (it's already the default's own behaviour) so an existing script/README
    # snippet that still passes it never breaks.
    ap.add_argument("--with-demo", action="store_true",
                    help="also load the small labelled [SAMPLE] demo fixture (same as AJP_LOAD_DEMO=1)")
    ap.add_argument("--no-demo", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    load_demo = (args.with_demo or os.environ.get("AJP_LOAD_DEMO") == "1") and not args.no_demo

    data_dir = Path(args.data_dir)
    pgdata = data_dir / "pgdata"
    rawstore = data_dir / "raw"
    pgdata.mkdir(parents=True, exist_ok=True)
    rawstore.mkdir(parents=True, exist_ok=True)

    import pgserver
    server = pgserver.get_server(str(pgdata))
    dsn = server.get_uri()
    apply_migrations(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        counts = bootstrap.bootstrap_all(conn, LocalFsStore(rawstore), demo=load_demo)
    print(f"seeded: {counts['orgs_base']} base + {counts['orgs_supplement']} supplement = "
         f"{counts['orgs_total']} orgs, {counts['demo_listings']} demo listings")
    print(f"DSN={dsn}")
    print(f"export AJP_DSN='{dsn}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
