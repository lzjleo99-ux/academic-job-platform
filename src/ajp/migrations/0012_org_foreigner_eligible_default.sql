-- R5: a military/police employer's board defaults every row's foreigner_eligible to 'no' rather than 'unknown',
-- since the ad text alone (title/teaser; documents are never opened, D6) essentially never states citizenship
-- terms explicitly. Org-level, not row-level: set once per organisation (e.g. va, KPU's future non-deeplink
-- successor), read by board.rows_from_items() at crawl time. NULL = the ordinary 'unknown' default for every
-- other organisation - never widen this beyond the seed's own explicit opt-in.
ALTER TABLE org_master ADD COLUMN foreigner_eligible_default text
  CHECK (foreigner_eligible_default IS NULL OR foreigner_eligible_default IN ('yes', 'no', 'unknown'));
