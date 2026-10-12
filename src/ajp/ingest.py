"""Listing ingestion gate: no row enters ``listing`` without a valid raw envelope.

The envelope is the sidecar written by ``rawstore.put_raw`` for ``listing.raw_html_ref``:
source_id must match the row, http_status must be 2xx, fetched_at must parse and
robots_allowed must be exactly ``true``. Channel sources (deeplink-only) are refused.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

import psycopg
from psycopg.types.json import Jsonb

from ajp.applicants.profile_extract import extract_listing_skill_terms, infer_listing_disciplines_with_zone
from ajp.dedupe_keys import canonicalize_url, title_hash
from ajp.rawstore import ObjectStore, get_raw

# Insertable listing columns. Managed by the database or by upsert_listing_deduped and therefore not accepted
# here: id, salary_sig (generated), first_seen, last_seen, updated_at, sources (T13 cross-source dedupe - an
# append-only array upsert_listing_deduped maintains itself, never a plain overwritable field a row dict sets),
# inferred_disciplines/inferred_skill_terms/inferred_at (T39, packet 022 - upsert_listing itself computes
# these from the row's own title_raw/requirements_raw/raw_text via the SAME shared functions the on-the-fly
# matching scorer uses, see its own comment below; a connector passing them directly would risk a stale or
# hand-computed value silently diverging from that single source of truth).
LISTING_COLUMNS = (
    "ext_uid", "source_id", "url", "url_canon", "title_raw", "title_norm_hash", "org_id", "org_raw",
    "country_iso2", "city", "lat", "lon", "disciplines_euroscivoc", "position_kind", "rs_research_title",
    "contract", "ft_pt", "duration_months_v", "duration_months_conf", "duration_months_raw", "hours_pct",
    "study_while_work_ok_v", "study_while_work_ok_conf", "study_while_work_ok_raw",
    "salary_min", "salary_max", "salary_currency", "salary_period", "salary_gross_net", "salary_conf",
    "salary_raw", "degree_required", "language", "foreigner_eligible", "requirements_raw",
    "contact_emails", "contact_phones", "contact_name", "contact_scope", "apply_url", "deadline",
    "posted_at", "posted_at_basis", "posted_at_conf", "posted_at_raw", "ad_lang", "raw_text", "raw_html_ref", "status", "kind", "extraction_family",
    "location_class", "location_class_conf", "location_class_raw",             # T23 (PM ruling R2)
    "content_scanned",                                                        # T31 (packet 019)
    # T48 (packet 028): the classification's own {conf, raw}, closing the last D6 gap on this table -
    # kind and position_kind were the only two extracted values with neither (0026). Accepted from the
    # connector because only the connector knows which classifier it used (rs_text vs intl_text, chosen
    # per source) - unlike 0024's inferred_*, these CANNOT be recomputed here from the row's own text.
    # Optional: a connector not yet wired to the evidence-returning classifiers simply leaves them NULL,
    # and no row is ever refused for lacking evidence it was never asked for.
    "kind_conf", "kind_raw", "position_kind_conf", "position_kind_raw",
)
# T23 (PM ruling R2): country_iso2 is no longer required - a row whose country cannot be resolved is
# persisted with country_iso2=None + a real location_class (remote/hybrid/unknown) instead of being
# discarded outright; listing.country_iso2 itself was made nullable in the same migration (0020) that added
# location_class, so the DB layer and this ingest-time check move together, never one without the other.
REQUIRED = ("ext_uid", "source_id", "url", "title_raw", "position_kind", "raw_html_ref")


class IngestRefused(ValueError):
    pass


def check_envelope(store: ObjectStore, key: str, source_id: str) -> Dict[str, Any]:
    try:
        _, meta = get_raw(store, key)
    except (KeyError, FileNotFoundError, ValueError, OSError) as e:
        raise IngestRefused(f"raw object {key!r} missing or corrupt: {e}") from e
    problems = []
    if meta.get("source_id") != source_id:
        problems.append(f"source_id {meta.get('source_id')!r} != {source_id!r}")
    if meta.get("robots_allowed") is not True:
        problems.append("robots_allowed is not true")
    status = meta.get("http_status")
    if not (isinstance(status, int) and 200 <= status < 300):
        problems.append(f"http_status {status!r} is not 2xx")
    if not meta.get("url"):
        problems.append("url missing")
    try:
        datetime.fromisoformat(str(meta.get("fetched_at")))
    except ValueError:
        problems.append("fetched_at missing or unparsable")
    if problems:
        raise IngestRefused(f"raw envelope for {key} rejected: " + "; ".join(problems))
    return meta


def upsert_listing(conn: psycopg.Connection, row: Mapping[str, Any], store: ObjectStore) -> Tuple[Any, bool]:
    """Insert or refresh one listing. Returns (listing_id, inserted)."""
    row = dict(row)
    unknown = set(row) - set(LISTING_COLUMNS)
    if unknown:
        raise IngestRefused(f"unknown or database-managed columns: {sorted(unknown)}")
    missing = [c for c in REQUIRED if not row.get(c)]
    if missing:
        raise IngestRefused(f"missing required fields: {missing}")
    kind = conn.execute("SELECT kind FROM source WHERE id = %s", (row["source_id"],)).fetchone()
    if kind is None:
        raise IngestRefused(f"unknown source {row['source_id']!r}")
    if kind[0] == "channel":
        raise IngestRefused(f"{row['source_id']!r} is a deeplink channel; its results are never ingested")
    check_envelope(store, row["raw_html_ref"], row["source_id"])

    row.setdefault("url_canon", canonicalize_url(row["url"]))
    row.setdefault("title_norm_hash", title_hash(row["title_raw"]))
    if "language" in row:
        row["language"] = Jsonb(row["language"])

    # T39 (packet 022): computed HERE, in the one shared row-build path every connector family already goes
    # through (upsert_listing_deduped -> upsert_listing), from the SAME functions ajp.applicants.matching's
    # own on-the-fly scorer uses - never a second, drifting copy of this logic, and never a per-connector
    # edit needed to pick it up. Persisted so a future matches request can read a plain column instead of
    # re-running this exact scan against every one of thousands of open listings (T36f's own profiling found
    # the discipline-bucket regex scan the dominant per-request cost at the live DB's real scale).
    buckets, disc_zone = infer_listing_disciplines_with_zone(
        row.get("title_raw") or "", row.get("requirements_raw") or "", row.get("raw_text") or "")
    row["inferred_disciplines"] = Jsonb({"buckets": buckets, "zone": disc_zone})
    row["inferred_skill_terms"] = Jsonb(extract_listing_skill_terms(
        row.get("title_raw") or "", row.get("requirements_raw") or "", row.get("raw_text") or ""))
    row["inferred_at"] = datetime.now(timezone.utc)

    cols = list(row)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("source_id", "ext_uid"))
    sql = (f"INSERT INTO listing ({', '.join(cols)}) VALUES ({', '.join('%(' + c + ')s' for c in cols)}) "
           f"ON CONFLICT (source_id, ext_uid) DO UPDATE SET {updates}, last_seen = now() "
           f"RETURNING id, (xmax = 0)")
    listing_id, inserted = conn.execute(sql, row).fetchone()
    return listing_id, inserted


# ---------------------------------------------------------------- T13: cross-source dedupe -----------------
# One expression, used by BOTH the merge and the re-crawl branch: refresh this source's own last_seen when
# sources[] already records it, otherwise append a new entry for it.
#
# F14 (found 2026-10-11): the re-crawl branch used to be a bare
#   sources = (SELECT jsonb_agg(...) FROM jsonb_array_elements(sources) s)
# which is NULL whenever sources[] is EMPTY - jsonb_array_elements('[]'::jsonb) yields no rows at all, and
# jsonb_agg over zero rows is NULL, not '[]'. That UPDATE then violated listing.sources' own NOT NULL
# constraint (0014) with a hard NotNullViolation, which killed the WHOLE source's run (one bad row aborts
# every other row in that crawl, and three such runs block the source entirely). Rows with sources='[]' are
# not hypothetical: every listing that predates migration 0014 (its DEFAULT, with no backfill UPDATE) has
# one, and so does every row board.crawl_orgs() wrote through plain upsert_listing (see below).
# The CASE/EXISTS shape handles the empty array for free: no entry for this source -> append one.
_SOURCES_APPEND_OR_REFRESH = """(
     CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(sources) s WHERE s->>'source' = %(sid)s)
          THEN (SELECT jsonb_agg(CASE WHEN s->>'source' = %(sid)s
                                      THEN s || jsonb_build_object('last_seen', %(now)s::text)
                                      ELSE s END)
                FROM jsonb_array_elements(sources) s)
          ELSE sources || %(new_entry)s::jsonb END)"""
def find_cross_source_duplicate(conn: psycopg.Connection, row: Mapping[str, Any]) -> Optional[Any]:
    """The id of an EXISTING listing from a DIFFERENT source this row duplicates, by (a) an exact canonical-URL
    match, or (b) the same normalized title + employer, at the same city, with a posted/deadline date within
    +/-5 days of each other - or None (a genuinely new listing). Matches PM's own T13 dedupe rule; (b) is only
    attempted when there is a title, an employer AND a date to match on - too little signal is never guessed
    into a merge."""
    url_canon = row.get("url_canon") or canonicalize_url(row["url"])
    r = conn.execute("SELECT id FROM listing WHERE url_canon = %s AND source_id != %s LIMIT 1",
                     (url_canon, row["source_id"])).fetchone()
    if r:
        return r[0]
    date_val = row.get("posted_at") or row.get("deadline")
    if not (row.get("org_raw") and date_val):
        return None
    title_norm_hash = row.get("title_norm_hash") or title_hash(row["title_raw"])
    r = conn.execute(
        """SELECT id FROM listing
            WHERE source_id != %s AND title_norm_hash = %s AND org_raw = %s
              AND coalesce(city, '') = coalesce(%s, '')
              AND abs(extract(epoch FROM (coalesce(posted_at, deadline) - %s::timestamptz))) <= 5 * 86400
            LIMIT 1""",
        (row["source_id"], title_norm_hash, row["org_raw"], row.get("city"), date_val)).fetchone()
    return r[0] if r else None


def upsert_listing_deduped(conn: psycopg.Connection, row: Mapping[str, Any], store: ObjectStore) -> Tuple[Any, str]:
    """T13: like upsert_listing, but checks for a cross-source duplicate FIRST. If one exists, this row is
    never inserted as a separate listing - {source, url, first_seen, last_seen} is appended to the EXISTING
    canonical row's sources[] (or, if that source is already recorded there, e.g. a re-crawl finding the same
    posting again, only its last_seen is refreshed - T14 needs a real per-source last_seen for the API's
    sources[] shape, not just the listing row's own last_seen), and the canonical row's own fields are never
    touched by the merge (acceptance: 'sources[] merge never overwrites an existing canonical row'). Returns
    (listing_id, action): 'inserted' (genuinely new, sources[] seeded with its own entry), 'merged' (a
    cross-source duplicate, appended or refreshed), or 'refreshed' (a same-source re-crawl that matched by
    (source_id, ext_uid) instead - upsert_listing's own ON CONFLICT path already updates everything else)."""
    row = dict(row)
    now = datetime.now(timezone.utc).isoformat()
    dup_id = find_cross_source_duplicate(conn, row)
    entry = [{"source": row["source_id"], "url": row["url"], "first_seen": now, "last_seen": now}]
    if dup_id is not None:
        conn.execute(
            f"UPDATE listing SET last_seen = now(), sources = {_SOURCES_APPEND_OR_REFRESH} WHERE id = %(id)s",
            {"sid": row["source_id"], "now": now, "id": dup_id, "new_entry": Jsonb(entry)})
        return dup_id, "merged"
    listing_id, inserted = upsert_listing(conn, row, store)
    if inserted:
        conn.execute(
            "UPDATE listing SET sources = %s::jsonb WHERE id = %s", (Jsonb(entry), listing_id))
    else:
        # F14: the same shape as the merge branch above, never a bare jsonb_agg (see the constant's comment).
        conn.execute(
            f"UPDATE listing SET sources = {_SOURCES_APPEND_OR_REFRESH} WHERE id = %(id)s",
            {"sid": row["source_id"], "now": now, "id": listing_id, "new_entry": Jsonb(entry)})
    return listing_id, "inserted" if inserted else "refreshed"
