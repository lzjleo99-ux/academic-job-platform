-- T39 (packet 022): persist the applicant-matching engine's own listing-side inference at ingest time
-- instead of recomputing it from title/requirements_raw/raw_text on every single applicant's matches
-- request (T36f's own cProfile run found the 6-bucket discipline regex scan the dominant per-request cost
-- against the live ~4,065-open-row DB). Both columns are nullable - every row already ingested before this
-- migration (and any connector/code path this project has not yet wired the computation into) simply has
-- NULL here, and ajp.applicants.matching's own scorer falls back to the identical on-the-fly computation
-- whenever either column is NULL, exactly as it always has (see matching.py's own comments) - this is a
-- pure, additive perf column, never a new source of truth that could silently diverge from the real
-- extraction functions (both are filled by calling those SAME shared functions, never a second copy).
--
-- inferred_disciplines: {"buckets": [...6-bucket labels...], "zone": "title"|"body"|null} - the exact,
-- real return of ajp.applicants.profile_extract.infer_listing_disciplines(), zone-tagged (title is always
-- checked first and, if it alone names a bucket, trusted outright - see that function's own docstring).
-- inferred_skill_terms: [{"term": "...", "zone": "title"|"requirements"|"body"}, ...] - every SPECIFIC,
-- non-generic DISCIPLINE_KEYWORDS-vocabulary term (T37's own stoplist already excluded) actually present in
-- this listing's own title/requirements_raw/raw_text, zone-tagged by the first (highest-priority) zone it
-- appears in - the SAME vocabulary/zone-priority scheme ajp.applicants.matching._score_skill_relevance
-- already uses for an applicant's own `discipline_terms` field, precomputed here so a match request never
-- has to re-run that scan per listing.
ALTER TABLE listing ADD COLUMN inferred_disciplines jsonb;
ALTER TABLE listing ADD COLUMN inferred_skill_terms jsonb;
ALTER TABLE listing ADD COLUMN inferred_at timestamptz;
-- No backfill in this migration itself - T39's own one-off backfill script (tools/backfill_inferred_signals.py)
-- runs separately, over the real live DB, and is re-run (not just run once) whenever the underlying keyword
-- vocabulary changes, which a single forward-only migration file is the wrong place to re-trigger.
