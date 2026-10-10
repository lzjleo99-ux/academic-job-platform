"""Minimal forward-only SQL migration runner.

Each ``migrations/NNNN_name.sql`` runs in its own transaction and is recorded in
``schema_migrations`` with a sha256 checksum. An applied file that has since been edited
is an error: add a new migration instead.

    DATABASE_URL=postgresql://... python -m ajp.migrate
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_LOCK_KEY = 0x616A7001  # advisory lock so concurrent runners serialize


class MigrationError(RuntimeError):
    pass


def discover(directory: Path = MIGRATIONS_DIR) -> List[Path]:
    files = sorted(p for p in directory.iterdir() if p.suffix == ".sql")
    for p in files:
        if not _NAME_RE.match(p.name):
            raise MigrationError(f"bad migration filename: {p.name}")
    versions = [p.name[:4] for p in files]
    if len(set(versions)) != len(versions):
        raise MigrationError("duplicate migration version number")
    return files


def apply_migrations(dsn: str, directory: Path = MIGRATIONS_DIR) -> List[str]:
    """Apply pending migrations; return the filenames applied by this call."""
    files = discover(directory)
    applied_now: List[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                     version    text PRIMARY KEY,
                     name       text NOT NULL,
                     checksum   text NOT NULL,
                     applied_at timestamptz NOT NULL DEFAULT now())"""
            )
            done = {
                row[0]: row[1]
                for row in conn.execute("SELECT version, checksum FROM schema_migrations")
            }
            for path in files:
                version = path.name[:4]
                sql = path.read_bytes()
                checksum = hashlib.sha256(sql).hexdigest()
                if version in done:
                    if done[version] != checksum:
                        raise MigrationError(
                            f"{path.name} was modified after being applied "
                            f"(recorded {done[version][:12]}, now {checksum[:12]})"
                        )
                    continue
                with conn.transaction():
                    conn.execute(sql.decode("utf-8"))
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                        (version, path.name, checksum),
                    )
                applied_now.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return applied_now


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args(argv)
    if not args.dsn:
        ap.error("set DATABASE_URL or pass --dsn")
    applied = apply_migrations(args.dsn)
    print("applied:", ", ".join(applied) if applied else "nothing (up to date)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
