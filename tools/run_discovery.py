"""T20: runs the two-stage bounded discovery (ajp.discovery) against every RS org missing career_urls,
persists every attempt (record_run) and every real hit (record_discovery) to a real Postgres DB, then writes
pm/outbox/coverage.json - the same DB/migrations dev_up.py uses, so this can be re-run against the dev stack
or a throwaway one. Reports ACTUAL counts only (this module's own docstring promise), never an extrapolation.

Seeds BOTH the base rs_orgs.json (100 orgs) AND rs_orgs_openalex_supplement.json (the T20 OpenAlex
reconciliation's own MISSING orgs, see pm/recon/t20_rs_openalex_reconciliation.json) - the full RS org
backbone this task is actually scoped to, not just the pre-existing 100.

Idempotent/resumable (PM correction, 2026-09-23): an org already carrying career_urls, OR one that already
has a discovery_run_log row from an earlier invocation against the SAME --pgdata, is skipped outright - a
second run over the same persistent --pgdata only probes what the first one didn't reach (e.g. because it
crashed partway through). Pass --pgdata explicitly to get this; without it, a fresh throwaway DB is created
and destroyed as before (nothing to resume, by definition). A throwaway DB this script created itself is
still cleaned up on exit; one the caller passed via --pgdata is NEVER deleted, exactly so it can be resumed
or audited afterwards.

    python tools/run_discovery.py [--pgdata PATH] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402
import pgserver  # noqa: E402

from ajp import discovery, orgs  # noqa: E402
from ajp.connectors.base import PoliteClient  # noqa: E402
from ajp.migrate import apply_migrations  # noqa: E402
from ajp.rawstore import LocalFsStore  # noqa: E402

USER_AGENT = "ajp-research-bot/0.1.0 (non-commercial academic job aggregator; contact-not-configured@example.invalid)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pgdata", default=None, help="reuse an existing pgdata dir instead of a throwaway one")
    ap.add_argument("--limit", type=int, default=None, help="cap how many RS orgs to probe (debugging only)")
    ap.add_argument("--out", default="/tmp/ajp/pm/outbox/coverage.json")
    args = ap.parse_args()

    # cleanup_mode='stop' (pgserver's own default, kept explicit here) for a caller-supplied --pgdata -
    # the server stops but the directory (and everything discovery wrote to it) survives for a later
    # resume/audit. A throwaway dir this script created itself uses 'delete' so nothing is left behind.
    reused_pgdata = args.pgdata is not None
    pgdata = args.pgdata or tempfile.mkdtemp(prefix="ajp-discovery-pgdata-")
    db = pgserver.get_server(pgdata, cleanup_mode="stop" if reused_pgdata else "delete")
    try:
        dsn = db.get_uri()
        apply_migrations(dsn)
        store = LocalFsStore(tempfile.mkdtemp(prefix="ajp-discovery-store-"))
        client = PoliteClient(rps=1, user_agent=USER_AGENT)

        supplement_path = ROOT / "src" / "ajp" / "seeds" / "rs_orgs_openalex_supplement.json"
        supplement = json.loads(supplement_path.read_text(encoding="utf-8"))["orgs"] if supplement_path.exists() else []

        with psycopg.connect(dsn, autocommit=True) as conn:
            seed = orgs.load_seed()
            orgs.seed_orgs(conn, seed, country="RS")
            if supplement:
                orgs.seed_orgs(conn, supplement, country="RS")
                print(f"also seeded {len(supplement)} T20 OpenAlex-reconciliation supplement orgs", file=sys.stderr)
            already_probed = {r[0] for r in conn.execute(
                "SELECT DISTINCT org_slug FROM discovery_run_log").fetchall()}
            targets = [o for o in seed + supplement
                      if not o.get("career_urls") and o.get("slug") not in already_probed]
            skipped = len(seed) + len(supplement) - len(targets)
            if args.limit:
                targets = targets[: args.limit]
            print(f"probing {len(targets)} RS orgs missing career_urls (base seed + supplement); "
                 f"{skipped} already had career_urls or a prior run_log row, skipped", file=sys.stderr)

            n_runs = n_found = n_soft404 = n_unreachable = 0
            for o in targets:
                slug = o.get("slug") or o.get("name_en", "").lower().replace(" ", "-")
                homepage = o.get("homepage")
                if not homepage:
                    continue
                result = discovery.discover_career_paths(client, store, slug, homepage)
                discovery.record_run(conn, result)
                n = discovery.record_discovery(conn, result)
                n_runs += 1
                n_found += n
                n_soft404 += result.method == "soft_404_host"
                n_unreachable += result.method == "homepage_unreachable"
                print(f"{slug:28s} {result.method:22s} fetched={result.paths_fetched:2d} "
                     f"found={len(result.found_urls)}", file=sys.stderr)

            report = discovery.coverage_report(conn)
            report["run_summary"] = {
                "orgs_probed": n_runs, "urls_discovered": n_found,
                "soft_404_hosts": n_soft404, "unreachable_hosts": n_unreachable,
            }
            recon_path = Path("/tmp/ajp/pm/recon/t20_rs_openalex_reconciliation.json")
            if recon_path.exists():
                recon = json.loads(recon_path.read_text(encoding="utf-8"))
                report["openalex_reconciliation"] = {
                    "total_openalex_rs_institutions": recon["counts"]["total_openalex_rs_institutions"],
                    "represented": recon["counts"]["represented"], "consolidated": recon["counts"]["consolidated"],
                    "out_of_scope": recon["counts"]["out_of_scope"], "missing_added": recon["counts"]["missing_added"],
                    "identity_check_119": recon["counts"]["identity_check"],
                    "seed_total_after_supplement": len(seed) + len(supplement),
                }
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"wrote {out_path}", file=sys.stderr)
            print(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        db.cleanup()            # honours the cleanup_mode set above - 'stop' never deletes a reused --pgdata
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
