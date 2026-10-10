"""`ajp-api` (T21 console script): starts the read-only FastAPI app (ajp.api.app) against the SAME
persisted embedded Postgres ajp-init created, bound to 127.0.0.1 (localhost-only, packet's own constraint -
"no production deployment in this milestone") - never 0.0.0.0, not overridable via a flag on purpose.

    ajp-api [--port 8000] [--data-dir PATH]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from ajp.cli.init import DEFAULT_DATA_DIR

LOCALHOST = "127.0.0.1"          # never a flag - see module docstring


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    import pgserver
    server = pgserver.get_server(str(data_dir / "pgdata"))
    os.environ["AJP_DSN"] = server.get_uri()
    print(f"http://{LOCALHOST}:{args.port}/docs")
    uvicorn.run("ajp.api.app:app", host=LOCALHOST, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
