-- q2 (approved): the 120-day undated-post staleness rule and the filename-derived-date guard are DISPLAY concerns
-- only. Neither ever deletes or hides a row: 'expired' and 'unknown' are ordinary status values a client can
-- filter on (default view = 'open'; a status filter can show 'expired'/'unknown' too). See board.py.
ALTER TABLE listing DROP CONSTRAINT listing_status_check;
ALTER TABLE listing ADD CONSTRAINT listing_status_check CHECK (status IN ('open','closed','expired','unknown'));

-- q1 (approved) + parity ruling (2026-09-22): posted_at may be read from a document's FILE NAME (never its
-- directory), or from a sibling paragraph that is ONLY a date immediately before the item's own block on a
-- rendered board (agrif.bg.ac.rs: one <p>dd.mm.yyyy.</p> right before <p>title ... link</p>), when nothing more
-- direct states it. A directly stated date elsewhere (a <time> element, an 'article'/'wp_permalink' item's own
-- text) does not populate these columns, matching D6's rule that a value needs support only when it is uncertain.
ALTER TABLE listing
  ADD COLUMN posted_at_basis text CHECK (posted_at_basis IN ('filename', 'visible_text')),
  ADD COLUMN posted_at_conf  real CHECK (posted_at_conf IS NULL OR posted_at_conf BETWEEN 0 AND 1),
  ADD COLUMN posted_at_raw   text;
ALTER TABLE listing ADD CONSTRAINT listing_posted_at_basis_has_support
  CHECK (posted_at_basis IS NULL OR (posted_at_conf IS NOT NULL AND posted_at_raw IS NOT NULL));
