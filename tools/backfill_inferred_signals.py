"""T39 (packet 022): one-off backfill of listing.inferred_disciplines/inferred_skill_terms/inferred_at for
every row already in the live DB before migration 0024 existed. Uses the SAME shared functions
ajp.ingest.upsert_listing itself calls on every future insert/re-crawl - never a second, drifting copy of
that logic - so a row backfilled by this script and a row freshly ingested afterwards always agree.

Safe to re-run (idempotent: recomputes and overwrites, never duplicates) - re-run it again whenever the
underlying DISCIPLINE_KEYWORDS/stoplist vocabulary changes, so every row stays in sync with the real
extraction logic rather than drifting to whatever vocabulary existed the last time this ran.

    python tools/backfill_inferred_signals.py [--data-dir PATH] [--batch-size N] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ajp.applicants.profile_extract import extract_listing_skill_terms, infer_listing_disciplines_with_zone  # noqa: E402
from ajp.cli.init import DEFAULT_DATA_DIR  # noqa: E402


def backfill(dsn: str, batch_size: int = 500, dry_run: bool = False) -> int:
    n = 0
    with psycopg.connect(dsn, autocommit=not dry_run) as conn:
        rows = conn.execute(
            "SELECT id, title_raw, requirements_raw, raw_text FROM listing ORDER BY id"
        ).fetchall()
        updates = []
        for listing_id, title_raw, requirements_raw, raw_text in rows:
            buckets, zone = infer_listing_disciplines_with_zone(title_raw or "", requirements_raw or "", raw_text or "")
            skill_terms = extract_listing_skill_terms(title_raw or "", requirements_raw or "", raw_text or "")
            updates.append((
                Jsonb({"buckets": buckets, "zone": zone}), Jsonb(skill_terms),
                datetime.now(timezone.utc), listing_id,
            ))
        if dry_run:
            print(f"[dry-run] would update {len(updates)} rows")
            return len(updates)
        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]
            conn.cursor().executemany(
                "UPDATE listing SET inferred_disciplines=%s, inferred_skill_terms=%s, inferred_at=%s WHERE id=%s",
                batch,
            )
            n += len(batch)
            print(f"  ... {n}/{len(updates)}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import pgserver
    server = pgserver.get_server(str(Path(args.data_dir) / "pgdata"))
    dsn = server.get_uri()
    n = backfill(dsn, batch_size=args.batch_size, dry_run=args.dry_run)
    print(f"backfilled {n} listing rows" + (" (dry-run, no writes)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
