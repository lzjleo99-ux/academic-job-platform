"""T25: ties store + profile_extract + matching together for the API layer - the same 'one function per
user-facing action, the router is a thin HTTP wrapper' convention ajp.thesis.pipeline already established.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from ajp.api import salary
from ajp.applicants import store
from ajp.applicants.matching import match_sort_key, score_listing
from ajp.applicants.profile_extract import parse_cv_and_extract_profile
from ajp.thesis.parser import ParseError


def upload_cv(conn: psycopg.Connection, root: Path, *, filename: str, content: bytes) -> store.Applicant:
    applicant_id, rel_path, source_format = store.save_upload(root, filename, content)
    store.create_applicant(conn, applicant_id=applicant_id, cv_filename=filename,
                           source_format=source_format, file_path=rel_path)
    try:
        full_text, profile = parse_cv_and_extract_profile(root / rel_path)
        store.set_profile(conn, applicant_id, cv_raw_text=full_text, profile=profile)
    except ParseError as e:
        store.set_failed(conn, applicant_id, str(e))
    return store.get_applicant(conn, applicant_id)


# Every OPEN listing, once per matches request - the N+1 guard (packet's own explicit requirement): this is
# the ONLY listing query the whole matching flow issues, regardless of how many listings exist; every score
# is then computed in pure Python (ajp.applicants.matching.score_listing, no further queries per listing).
#
# T37 (packet 022), found live building this exact task - a real, previously-undiscovered bug: this SELECT
# never included `requirements_raw` at all, so _score_skill_relevance's/infer_listing_disciplines's own
# "requirements" zone has been silently dead code against every real listing in production - the field was
# always None here regardless of what the row's own column actually held. 265 of the live DB's 4,065 open
# rows (checked directly) have real, non-empty requirements_raw - genuine signal that was simply never
# reaching the scorer. `posted_at` is new too, for match_sort_key's own R4 tie-break (T37).
#
# T39 (packet 022, perf): `inferred_disciplines`/`inferred_skill_terms` are new - the persisted, ingest-time
# precomputation ajp.applicants.matching._listing_disciplines() reads directly when present, in place of
# re-running the live keyword scan on every single matches request (see that function's own comment). A row
# ingested before migration 0024, or not yet covered by the one-off backfill, simply has NULL here - the
# scorer's own fallback is unchanged, so this is purely additive.
_OPEN_LISTINGS_SQL = f"""
    SELECT l.id, l.title_raw, l.raw_text, l.requirements_raw, l.disciplines_euroscivoc, l.degree_required,
           l.country_iso2, l.location_class, l.ft_pt, l.language, l.position_kind, l.posted_at,
           l.inferred_disciplines, l.inferred_skill_terms,
           {salary.eur_year_sql('min')} AS salary_eur_year_min,
           {salary.eur_year_sql('max')} AS salary_eur_year_max
    FROM listing l
    LEFT JOIN fx_rate_latest fx ON fx.currency = l.salary_currency AND l.salary_currency <> 'EUR'
    WHERE l.status = 'open'
"""


def compute_matches(conn: psycopg.Connection, applicant_id: UUID) -> Optional[List[Dict[str, Any]]]:
    """Recomputes every match for this applicant against every currently-OPEN listing, replacing (never
    accumulating) whatever applicant_match rows existed before - a stale score from an earlier profile
    version or a listing that has since closed must never linger. Returns the ranked list (highest score
    first), or None if the applicant does not exist (a 404 for the router to report, not a crash). This is
    still the ONE full-table pass (the N+1 guard, packet 016's own explicit requirement) - T33's own
    pagination (see get_matches) applies only to what the API then RETURNS, never to this scoring pass
    itself, which must score every open listing regardless to rank them at all."""
    applicant = store.get_applicant(conn, applicant_id)
    if applicant is None:
        return None
    cur = conn.cursor(row_factory=dict_row)
    listings = cur.execute(_OPEN_LISTINGS_SQL).fetchall()
    results = []
    listing_by_id: Dict[str, Dict[str, Any]] = {}
    for listing in listings:
        listing = dict(listing)
        outcome = score_listing(applicant.profile, listing)
        results.append({"listing_id": str(listing["id"]), **outcome})
        listing_by_id[str(listing["id"])] = listing
    # T43 (packet 025, perf): DELETE + the bulk persist below are now one explicit transaction (psycopg3's
    # own `conn.transaction()` opens a real BEGIN/COMMIT/ROLLBACK block even on an autocommit connection,
    # see ajp.api.deps.get_conn's own `autocommit=True`) - "a failure must not leave zero/partial rows"
    # (PM's own explicit requirement) was NOT actually true before this (autocommit meant the DELETE and
    # each row of the old row-by-row executemany INSERT were each their OWN separately-committed
    # statement) - a real, found-live gap this task's own requirement surfaced, fixed as part of making
    # the persistence layer genuinely atomic, not a scoring/matching semantic change.
    #
    # T43's own real finding, measured directly (not assumed): a single multi-row `INSERT ... VALUES
    # (...),(...),...` (PM's own first-suggested approach) was tried FIRST and measured SLOWER than the
    # existing row-by-row `executemany` for this real data (~0.39-0.46s vs ~0.20-0.26s, isolated and
    # re-measured repeatedly) - psycopg3's own `executemany` already pipelines same-template statements
    # efficiently over the extended protocol, while one ~73KB statement with 16,260 parameters has its own
    # real parse cost that ate the round-trip savings. `COPY ... FROM STDIN` (PM's own explicitly offered
    # alternative) was tried next and IS genuinely faster (~0.15-0.20s, consistently, re-measured) - it
    # bypasses SQL statement parsing entirely, streaming rows in COPY's own binary wire format. This is
    # the one actually shipped; see pm/outbox/t43_report.md for the full comparison of all three.
    with conn.transaction():
        conn.execute("DELETE FROM applicant_match WHERE applicant_id = %s", (str(applicant_id),))
        if results:
            from psycopg.types.json import Jsonb
            with conn.cursor().copy(
                "COPY applicant_match (applicant_id, listing_id, score, factors) FROM STDIN"
            ) as copy:
                for r in results:
                    copy.write_row((
                        str(applicant_id), r["listing_id"], r["score"],
                        Jsonb({"factors": r["factors"], "counts": r["counts"], "coverage": r["coverage"],
                              "total_weight": r["total_weight"]}),
                    ))
    # T37 (packet 022, R4): the shared deterministic tie-break, not a bare score-only sort - two listings
    # tied on the exact score must still come back in the SAME order every time (see match_sort_key's own
    # docstring). get_matches's own SQL ORDER BY mirrors this same precedence for what the API actually
    # returns; this Python-side sort keeps THIS function's own return value (used directly by tests/scripts,
    # never by the router itself - see its own docstring) consistent with that.
    results.sort(key=lambda r: match_sort_key(r, listing_by_id[r["listing_id"]]))
    return results


def get_matches(conn: psycopg.Connection, applicant_id: UUID, *, limit: int = 100,
                offset: int = 0) -> Optional[Dict[str, Any]]:
    """The last COMPUTED matches (GET, never re-scores) - a fixed 2-query read (one count, one page):
    the applicant_match rows joined once against listing for display fields, nothing per-row beyond that.
    T33 (packet 018, Q4): a real applicant's own matches against a 4.7k-row DB must never be dumped
    unbounded on one response - paginated, ordered by score desc, with the real total count alongside the
    page (never silently truncated with no way to tell how many there really are)."""
    if store.get_applicant(conn, applicant_id) is None:
        return None
    cur = conn.cursor(row_factory=dict_row)
    total = cur.execute(
        "SELECT count(*) AS n FROM applicant_match WHERE applicant_id = %s", (str(applicant_id),)
    ).fetchone()["n"]
    # T37 (packet 022, R4): mirrors match_sort_key's own precedence exactly - score, then skill_relevance's
    # own points, then the number of positively-evidenced factors, then the listing's own posted_at, then
    # the listing id itself as the final, always-decisive tie-break (NULLS LAST on the two JSON-derived
    # numeric fields and on posted_at: a row that somehow lacks one, or a listing that never stated a
    # posted_at, must never WIN a tie against a row/listing that has real data there).
    rows = cur.execute(
        """SELECT am.listing_id, am.score, am.factors, am.computed_at,
                  l.title_raw, l.org_raw, l.url, l.apply_url, l.contact_emails, l.contact_phones,
                  l.country_iso2, l.city, l.location_class
           FROM applicant_match am JOIN listing l ON l.id = am.listing_id
           WHERE am.applicant_id = %s
           ORDER BY am.score DESC,
                    (am.factors->'factors'->'skill_relevance'->>'points')::numeric DESC NULLS LAST,
                    (am.factors->'counts'->>'positive')::int DESC NULLS LAST,
                    l.posted_at DESC NULLS LAST,
                    am.listing_id ASC
           LIMIT %s OFFSET %s""",
        (str(applicant_id), limit, offset),
    ).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        stored = r.pop("factors")            # {"factors": {...}, "counts": {...}, "coverage": ..., "total_weight": ...}
        r.update(factors=stored["factors"], counts=stored["counts"], coverage=stored["coverage"],
                total_weight=stored["total_weight"], listing_id=str(r["listing_id"]))
        out.append(r)
    return {"total": total, "results": out}
