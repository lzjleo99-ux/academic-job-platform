"""T20 'abroad' fan-out: fetches OpenAlex's own top-N-by-works_count institutions for a small set of
countries (type=education, works_count>=min_works, not healthcare-named - same filters as
orgs.openalex_review_queue's own review-queue convention), then runs the SAME two-stage bounded discovery
(ajp.discovery) against each org's own homepage.

HONEST SCOPE NOTE (2026-09-23): PM's own T20 spec asks for "top-20 by works_count per country" - there are
~195 countries in OpenAlex's own country_code vocabulary, so a literal top-20-per-country sweep is ~3,900
orgs; that is a recurring/background job's work (the scheduler, T21, not yet built/accepted), not a single
interactive session's. This tool demonstrates the real, working mechanism end to end on a SMALL, HONEST
sample (a handful of countries, a handful of orgs each, both configurable) and reports only what it actually
ran - never an extrapolated "top 20 x every country" figure presented as if it had been done.

    python tools/fetch_abroad_sample.py --countries DE,GB --top-n 5
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402
import pgserver  # noqa: E402

from ajp import discovery, orgs  # noqa: E402
from ajp.connectors.base import PoliteClient, UrlManifest  # noqa: E402
from ajp.migrate import apply_migrations  # noqa: E402
from ajp.rawstore import LocalFsStore, put_raw  # noqa: E402

USER_AGENT = "ajp-research-bot/0.1.0 (non-commercial academic job aggregator; contact-not-configured@example.invalid)"
FIELDS = "id,display_name,country_code,works_count,homepage_url,type"
OPENALEX_MANIFEST = UrlManifest(r"https://api\.openalex\.org/institutions\?[^ ]*")


def fetch_top_institutions(client: PoliteClient, store, country_code: str, top_n: int,
                           min_works: int = 50) -> List[Dict[str, Any]]:
    """Through PoliteClient, same governance every real request in this project goes through (robots,
    rate-limit, TLS, retry) - matching tools/match_openalex.py's own established convention for this exact
    API, not a second, ungoverned way of talking to it. No mailto sent - this project has no real configured
    contact address yet (operator_actions.md item 0), same deliberate choice match_openalex.py already made
    for the same reason. type=education + not healthcare-named, same filters as
    orgs.openalex_review_queue's own convention; a real homepage_url is required (nothing to probe otherwise
    - D6: never guessed)."""
    url = (f"https://api.openalex.org/institutions?filter=country_code:{country_code},type:education"
          f"&sort=works_count:desc&per-page={min(top_n * 2, 200)}&select={FIELDS}")
    res = client.get(url, manifest=OPENALEX_MANIFEST)
    put_raw(store, kind="json", source_id="openalex_institutions_abroad", url=url, content=res.content,
           fetched_at=res.fetched_at, status=res.status, content_type=res.content_type, robots_allowed=True)
    rows = json.loads(res.content)["results"]
    out = []
    for r in rows:
        if len(out) >= top_n:
            break
        if (r.get("works_count") or 0) < min_works:
            continue
        if orgs.is_healthcare_facility(r["display_name"]):
            continue
        if not r.get("homepage_url"):
            continue
        out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--countries", default="DE,GB", help="comma-separated ISO 3166-1 alpha-2 codes")
    ap.add_argument("--top-n", type=int, default=5, help="orgs per country (a real, small demonstration slice)")
    ap.add_argument("--out", default="/tmp/ajp/pm/outbox/abroad_sample.json")
    args = ap.parse_args()

    countries = [c.strip().upper() for c in args.countries.split(",") if c.strip()]

    pgdata = tempfile.mkdtemp(prefix="ajp-abroad-pgdata-")
    db = pgserver.get_server(pgdata, cleanup_mode="delete")   # always throwaway here - never left behind
    try:
        dsn = db.get_uri()
        apply_migrations(dsn)
        store = LocalFsStore(tempfile.mkdtemp(prefix="ajp-abroad-store-"))
        client = PoliteClient(rps=1, user_agent=USER_AGENT)

        all_orgs: List[Dict[str, Any]] = []
        for cc in countries:
            found = fetch_top_institutions(client, store, cc, args.top_n)
            print(f"{cc}: {len(found)} orgs (works_count>=50, type=education, real homepage)", file=sys.stderr)
            all_orgs.extend(found)

        with psycopg.connect(dsn, autocommit=True) as conn:
            results = []
            for o in all_orgs:
                slug = o["id"].rsplit("/", 1)[-1].lower()
                conn.execute(
                    """INSERT INTO org_master (slug, name_en, country, type, homepage, openalex_id, openalex_works_count)
                       VALUES (%s, %s, %s, 'university', %s, %s, %s) ON CONFLICT (slug) DO NOTHING""",
                    (slug, o["display_name"], o["country_code"], o["homepage_url"], o["id"], o["works_count"]),
                )
                result = discovery.discover_career_paths(client, store, slug, o["homepage_url"])
                discovery.record_run(conn, result)
                n = discovery.record_discovery(conn, result)
                print(f"{slug:30s} {o['country_code']} {result.method:22s} fetched={result.paths_fetched:2d} "
                     f"found={len(result.found_urls)}", file=sys.stderr)
                results.append({
                    "slug": slug, "name": o["display_name"], "country": o["country_code"],
                    "works_count": o["works_count"], "homepage": o["homepage_url"],
                    "method": result.method, "paths_fetched": result.paths_fetched,
                    "found_urls": result.found_urls,
                })

            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps({
                "scope_note": ("Honest small demonstration sample, NOT the full top-20-per-country sweep "
                              "(a recurring scheduler job, T21, not yet built) - see this tool's own docstring."),
                "countries_sampled": countries, "top_n_per_country": args.top_n,
                "orgs_attempted": len(all_orgs), "results": results,
            }, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"wrote {out_path}", file=sys.stderr)
    finally:
        db.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
