-- Fuzzy-lookup indexes; created only when pg_trgm is installed (see 0001).
-- A database migrated before pg_trgm was available will not get these retroactively:
-- add a new migration then instead of editing this one (checksums are enforced).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
    EXECUTE 'CREATE INDEX listing_title_trgm_idx ON listing USING gin (title_raw gin_trgm_ops)';
    EXECUTE 'CREATE INDEX org_master_name_en_trgm_idx ON org_master USING gin (name_en gin_trgm_ops)';
    EXECUTE 'CREATE INDEX org_master_name_native_trgm_idx ON org_master USING gin (name_native gin_trgm_ops)';
  END IF;
END $$;
