-- T12: OpenAlex's works_count is a coarse research-activity signal (not accreditation - org_master.accredited_rs
-- stays a separate, never-OpenAlex-populated field). Enrichment only, never touched by a reseed (see orgs.py's
-- SEED_COLUMNS comment); apply_openalex_works_count() below sets it once per org, like openalex_id itself.
ALTER TABLE org_master ADD COLUMN openalex_works_count integer CHECK (openalex_works_count >= 0);
