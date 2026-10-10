"""Daily FX snapshot job:  python -m ajp.fx [--provider ecb|erapi|all]

Providers run independently; one failing does not stop the others, and the exit code is
non-zero if any failed. NBS (official RSD middle rate) is not implemented yet: it needs
credentials (see outbox) and is planned for M2 (packet 002).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import List, Optional

from ajp.connectors.base import PoliteClient
from ajp.fx import ecb, erapi
from ajp.rawstore import LocalFsStore


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot FX rates into fx_rate")
    ap.add_argument("--provider", choices=["ecb", "erapi", "all"], default="all")
    ap.add_argument("--ecb-feed", choices=sorted(ecb.FEEDS), default="daily")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "data/raw"))
    args = ap.parse_args(argv)
    if not args.dsn:
        ap.error("set DATABASE_URL or pass --dsn")
    logging.basicConfig(level=logging.INFO)
    store = LocalFsStore(args.raw_root)
    polite = PoliteClient()
    results, failed = [], False
    for name in (["ecb", "erapi"] if args.provider == "all" else [args.provider]):
        try:
            results.append(ecb.run(args.dsn, args.ecb_feed, store, polite) if name == "ecb" else erapi.run(args.dsn, store, polite))
        except Exception as e:  # keep going; report per provider
            failed = True
            results.append({"provider": name, "error": f"{type(e).__name__}: {e}"})
    print(json.dumps(results, indent=2, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
