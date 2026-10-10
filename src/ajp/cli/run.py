"""`ajp-run` (T21 console script): the scheduler CLI over ajp.scheduler.

    ajp-run --once                    # one due-source pass, then exit
    ajp-run --daemon                  # loops until SIGTERM
    ajp-run --once --data-dir PATH    # a non-default install (must match the --data-dir ajp-init used)

Starts its OWN embedded-Postgres server against the SAME on-disk pgdata ajp-init created (pgserver's own
server process does not outlive the process that started it - each CLI invocation restarts it against the
persisted data directory, exactly like tools/dev_up.py already does)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from ajp.cli.init import DEFAULT_DATA_DIR
from ajp.connectors.base import PoliteClient
from ajp.rawstore import LocalFsStore
from ajp.scheduler import run_daemon, run_once


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--daemon", action="store_true")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--poll-seconds", type=float, default=60.0, help="--daemon only")
    ap.add_argument("--user-agent", default=None)
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    import pgserver
    server = pgserver.get_server(str(data_dir / "pgdata"))
    dsn = server.get_uri()
    store = LocalFsStore(data_dir / "raw")
    client = PoliteClient(rps=1, user_agent=args.user_agent) if args.user_agent else PoliteClient(rps=1)

    with psycopg.connect(dsn, autocommit=True) as conn:
        if args.once:
            result = run_once(conn, client, store, dsn)
            print(f"sources_due={result['sources_due']}")
            for r in result["results"]:
                print(f"  {r['source_id']:30s} {r['status']}"
                     + (f" rows_new={r.get('rows_new')} rows_updated={r.get('rows_updated')}"
                        if r["status"] == "ok" and "rows_new" in r else f" note={r.get('note') or r.get('error')}"))
            if result["fx"] and result["fx"].get("stale_warning"):
                print(f"FX WARNING: {result['fx']['stale_warning']}")
            return 0
        print("ajp-run --daemon: Ctrl-C or SIGTERM to stop")
        run_daemon(conn, client, store, dsn, poll_seconds=args.poll_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
