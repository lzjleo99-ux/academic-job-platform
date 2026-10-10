"""T20 'promote_honestly' step, HONEST SCOPE: for every 'discovered' row (0017_discovery_state), re-fetches
its own content and checks it against ats_detect.py's own patterns. A real match on an INGESTABLE family
(ats_workday_cxs/ats_greenhouse/ats_lever - the only families with a working connector) is recorded as
evidence (ats_family/ats_tenant columns) on the still-'discovered' row - this script does NOT itself
instantiate the family's connector and run a live dry-run/ingest cycle against the detected tenant; that is
its own scoped follow-up once a real match exists to build against (matching T18's own discipline: never
build ahead of a verified need). No row is promoted to 'active' by this script - discovery.promote() is
reserved for a REAL dry-run's schema-valid row count as evidence, which this script does not produce.
Reports ACTUAL counts only, including when zero ATS matches are found (the realistic outcome for Serbian
university/institute career pages, which are not typically ATS-hosted).

Requires a DB already populated by tools/run_discovery.py (same --pgdata, so the discovery_state rows and
the raw HTML this script re-fetches are found on a real host, not synthesised).

    python tools/promote_discovered.py --pgdata PATH
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402
import pgserver  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from ajp import discovery  # noqa: E402
from ajp.connectors.ats_detect import detect_one  # noqa: E402
from ajp.connectors.base import FetchFailed, ManifestViolation, PoliteClient, UrlManifest  # noqa: E402
from ajp.connectors.board import OrgManifest  # noqa: E402
from ajp.robots import RobotsDenied  # noqa: E402
from ajp.rawstore import LocalFsStore  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

USER_AGENT = "ajp-research-bot/0.1.0 (non-commercial academic job aggregator; contact-not-configured@example.invalid)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pgdata", required=True, help="the pgdata dir a prior run_discovery.py run wrote to")
    args = ap.parse_args()

    db = pgserver.get_server(args.pgdata)
    try:
        dsn = db.get_uri()
        store = LocalFsStore(tempfile.mkdtemp(prefix="ajp-promote-store-"))
        client = PoliteClient(rps=1, user_agent=USER_AGENT)

        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT org_slug, url FROM discovery_state WHERE state = 'discovered' ORDER BY org_slug"
            ).fetchall()
            print(f"checking {len(rows)} discovered rows for a known ATS family...", file=sys.stderr)

            n_checked = n_ats_found = n_ats_recorded = n_fetch_errors = 0
            for row in rows:
                org_slug, url = row["org_slug"], row["url"]
                try:
                    manifest = OrgManifest.for_hosts([urlsplit(url).netloc])
                except ValueError:
                    continue
                try:
                    res = client.get(url, manifest=manifest)
                except (FetchFailed, ManifestViolation, RobotsDenied):
                    n_fetch_errors += 1
                    continue
                n_checked += 1
                if res.status != 200:
                    continue
                html = res.content.decode("utf-8", "replace")
                match = detect_one(html, url)
                if not match:
                    continue
                n_ats_found += 1
                print(f"{org_slug:28s} {url[:60]:60s} -> {match.family} tenant={match.tenant}", file=sys.stderr)
                # Ingestable families only (workday/greenhouse/lever); detection-only families (smartrecruiters,
                # teamtailor, reachmee_varbi, successfactors, taleo, icims, jobteaser) are recorded as found but
                # never dry-run - no connector exists to run them through, matching T18's own do-not-build list.
                if match.family not in ("ats_workday_cxs", "ats_greenhouse", "ats_lever") or not match.tenant:
                    continue
                # A real dry run belongs to its own scoped follow-up (per-family connector instantiation,
                # live verification against the real tenant) - this run records the DETECTION as evidence
                # on the row (not a state change - discovery.promote() is reserved for real dry_run/active
                # transitions, never called here just to annotate a still-'discovered' row).
                conn.execute("UPDATE discovery_state SET ats_family = %s, ats_tenant = %s "
                             "WHERE org_slug = %s AND url = %s", (match.family, match.tenant, org_slug, url))
                n_ats_recorded += 1

            summary = (f"checked={n_checked} any_ats_pattern_matched={n_ats_found} "
                      f"ingestable_family_recorded={n_ats_recorded} promoted_to_active=0 "
                      f"fetch_errors={n_fetch_errors} "
                      f"(0 promotions: no real dry-run was run against a detected tenant this session - "
                      f"see this script's own docstring)")
            print(f"\n{summary}", file=sys.stderr)
            print(summary)
    finally:
        db.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
