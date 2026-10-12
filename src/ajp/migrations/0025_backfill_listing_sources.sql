-- F14 (2026-10-12): backfill listing.sources for every row still sitting at the T13 column's own '[]'
-- DEFAULT. Two populations, one UPDATE:
--   (a) every listing that predates 0014 - that migration added the column with DEFAULT '[]' and never
--       carried a backfill UPDATE of its own, so pre-T13 rows were born empty and stayed empty;
--   (b) every row board.crawl_orgs() wrote through plain upsert_listing before the F14 fix switched it to
--       upsert_listing_deduped - verified live in the deployed database: 935 such rows, all from
--       rs_org_boards, first_seen spanning 2026-09-25..2026-10-10, i.e. strictly before the fix shipped
--       (the newest row written after it already carries a healthy sources[]).
-- These are NOT harmless empties: the API's per-source join reads sources[], so a listing with '[]' is a
-- real, persisted, unfindable ad - it exists in the table and is invisible in every listing query.
--
-- The entry shape is the one upsert_listing_deduped itself writes (its own `entry` literal in ingest.py):
-- the row's own source_id and url, plus the row's own first_seen/last_seen. That reconstructs from data the
-- row already owns rather than inventing a "now" that would silently claim the ad was seen again today.
-- to_jsonb() renders the timestamptz as the same ISO-8601 string every healthy entry already stores.
--
-- Idempotent by construction (WHERE sources = '[]'), so it is a no-op both on a fresh install - 0014 runs
-- before any listing exists there - and on a re-run.
UPDATE listing
   SET sources = jsonb_build_array(
         jsonb_build_object(
           'source',     source_id,
           'url',        url,
           'first_seen', to_jsonb(first_seen),
           'last_seen',  to_jsonb(last_seen)))
 WHERE sources = '[]'::jsonb;
