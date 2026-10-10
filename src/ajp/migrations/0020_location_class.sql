-- T23 (PM ruling R2, acceptance T19/T20): a row whose country could not be resolved was previously always
-- DISCARDED by every connector (a "no_country" counter, never persisted) - country_iso2 was NOT NULL,
-- forcing that choice. R2: persist these rows instead, with country_iso2=null and a location_class bucket
-- (located/remote/hybrid/unknown) - never guess the country itself (D6 unchanged), but do not silently drop
-- a real, genuinely remote/unclassifiable ad just because no country was stated.
ALTER TABLE listing ALTER COLUMN country_iso2 DROP NOT NULL;
-- the existing CHECK (country_iso2 ~ '^[A-Z]{2}$') already permits NULL (a CHECK constraint is satisfied
-- automatically when any operand is NULL, per SQL's own three-valued logic) - no need to touch it.

ALTER TABLE listing ADD COLUMN location_class text NOT NULL DEFAULT 'unknown'
  CHECK (location_class IN ('located', 'remote', 'hybrid', 'unknown'));
-- D6: nullable + confidence + raw excerpt, same convention as every other extracted-not-stated field.
ALTER TABLE listing ADD COLUMN location_class_conf real CHECK (location_class_conf BETWEEN 0 AND 1);
ALTER TABLE listing ADD COLUMN location_class_raw text;

CREATE INDEX listing_location_class_idx ON listing (location_class);

-- Every row already ingested before this migration has a real, known country (the NOT NULL constraint
-- guaranteed that) - backfill them all as 'located' explicitly rather than leaving the column's own DEFAULT
-- 'unknown' imply otherwise for pre-existing data.
UPDATE listing SET location_class = 'located' WHERE country_iso2 IS NOT NULL;
