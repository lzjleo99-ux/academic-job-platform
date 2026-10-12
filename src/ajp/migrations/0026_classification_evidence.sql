-- T48 (packet 028): close the last D6 gap on `listing`, and give the classifier a review queue.
--
-- Every OTHER extracted value on this table already carries {conf, raw} - duration_months_v/_conf/_raw,
-- study_while_work_ok_v/_conf/_raw, salary_*/salary_conf/salary_raw, location_class/_conf/_raw (0020),
-- posted_at/_basis/_conf/_raw. `kind` and `position_kind` did not. Those two are not minor columns: they
-- are the platform's own classification output, and kind='other' is what HIDES a row by default, so they
-- were the two least auditable and most consequential values on the table - nothing recorded which text
-- span produced them, or how much the classifier trusted its own decision.
--
-- Both are nullable and additive, exactly like 0024's inferred_* columns: every row ingested before this
-- migration, and any code path not yet wired to the evidence-returning classifiers, simply has NULL here.
-- No backfill runs inside this migration (see 0024's own note - a forward-only file is the wrong place to
-- re-trigger one); tools/backfill_classification_evidence.py does that against the live DB.
--
-- conf is a RULE-STRENGTH PRIOR, not a calibrated probability - see ajp.extract.evidence's docstring. It
-- is deliberately NOT given a DEFAULT: 'unknown' must stay distinguishable from a real score, and 0 would
-- assert "certainly not a job", which is the opposite of what an unscored row means.
ALTER TABLE listing ADD COLUMN kind_conf          real CHECK (kind_conf BETWEEN 0 AND 1);
ALTER TABLE listing ADD COLUMN kind_raw           text;
ALTER TABLE listing ADD COLUMN position_kind_conf real CHECK (position_kind_conf BETWEEN 0 AND 1);
ALTER TABLE listing ADD COLUMN position_kind_raw  text;

-- D6 welded in, the same shape as listing_duration_has_raw / listing_swok_has_raw / listing_salary_has_raw:
-- a confidence number may never exist on its own. If there is a score there must be the excerpt it was
-- scored from, so the number can always be checked against the text by a human.
ALTER TABLE listing ADD CONSTRAINT listing_kind_has_raw CHECK
  (kind_conf IS NULL OR nullif(btrim(kind_raw), '') IS NOT NULL);
ALTER TABLE listing ADD CONSTRAINT listing_position_kind_has_raw CHECK
  (position_kind_conf IS NULL OR nullif(btrim(position_kind_raw), '') IS NOT NULL);

-- The review queue's own working index: "rows whose classification is weak enough to be worth a human".
-- Partial, so it stays empty-of-noise and cheap - a full index over ~4k rows where <10% need review is not
-- worth maintaining.
CREATE INDEX listing_low_kind_conf_idx ON listing (kind_conf) WHERE kind_conf IS NOT NULL AND kind_conf < 0.70;


-- ---------------------------------------------------------------- the review queue ---------------------
-- One row per (listing, field) classification a human is asked to look at. Not a second source of truth:
-- `listing` keeps the machine's answer, this table records WHY that answer was doubted and, once reviewed,
-- what the human decided instead. The machine's value is never overwritten by a review (PM's standing
-- ruling for the OpenAlex review queue: a human decision is recorded, never auto-applied) - human_kind
-- exists so the correction is captured and can be applied deliberately, in its own step, with its own
-- evidence.
CREATE TABLE classification_review (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id     uuid NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
  field          text NOT NULL CHECK (field IN ('kind', 'position_kind')),

  -- the machine's decision, copied at queue time so the queue stays readable even if the classifier's
  -- rules (or the listing's own text) change afterwards - a review is a record of a decision made ON a
  -- specific piece of evidence, not a live join.
  classified_as  text NOT NULL,
  conf           real CHECK (conf BETWEEN 0 AND 1),
  raw            text,
  rule           text,
  zone           text CHECK (zone IN ('title', 'body')),
  guards         jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(guards) = 'array'),
  -- which confidence model produced `conf` (ajp.extract.evidence.CONFIDENCE_MODEL). Stored because a
  -- score from an older model must never be silently read as if it came from the current one.
  model          text NOT NULL,

  reason         text NOT NULL CHECK (reason IN ('low_confidence', 'excluded_by_rule', 'unclassified_academic')),
  status         text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'confirmed', 'corrected', 'dismissed')),

  -- the human's answer. NULL while pending; NULL on 'confirmed' (the machine was right) and 'dismissed'
  -- (not worth reviewing); REQUIRED on 'corrected' - a correction with no corrected value is not a
  -- correction, and recording one would be worse than recording nothing.
  human_kind     text,
  reviewer       text,
  reviewed_at    timestamptz,
  notes          text,

  created_at     timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT classification_review_corrected_has_kind CHECK
    (status != 'corrected' OR nullif(btrim(human_kind), '') IS NOT NULL),
  CONSTRAINT classification_review_reviewed_is_stamped CHECK
    ((status = 'pending') = (reviewed_at IS NULL))
);

-- One open question per field per listing: re-queueing the same doubt must refresh the existing row, not
-- pile up duplicates that would each need dismissing by hand.
CREATE UNIQUE INDEX classification_review_open_idx
  ON classification_review (listing_id, field) WHERE status = 'pending';
CREATE INDEX classification_review_status_idx ON classification_review (status, created_at DESC);
CREATE INDEX classification_review_reason_idx ON classification_review (reason) WHERE status = 'pending';
