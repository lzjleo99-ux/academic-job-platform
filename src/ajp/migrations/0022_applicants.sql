-- T25 (packet 015/016): applicant CV upload + matching, localhost only. Mirrors thesis_task's own convention
-- (T16/T17) exactly: the file itself lives under data/applicants/{id}/ (local object storage, never uploaded
-- anywhere else by default), this table is task/profile metadata only. cv_raw_text is the full extracted text
-- (reused for re-extraction if the profile-parsing heuristics improve later, without re-uploading the file);
-- profile is the structured extraction result, always editable by the applicant afterward (PATCH), so a
-- heuristic mis-extraction is never a dead end. deleted_at is a real, queryable soft-delete: DELETE
-- /v1/applicants/{id} sets it (and erases the file + cv_raw_text, per the privacy page's own retention
-- promise) rather than removing the row outright, so an accidental double-delete is idempotent and a match
-- computed earlier can still explain what it matched against even after the source CV is gone. Never
-- deployed anywhere but localhost (same standing rule as thesis_task).
CREATE TABLE applicant (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status            text NOT NULL DEFAULT 'done' CHECK (status IN ('processing', 'done', 'failed')),
  error             text,
  cv_filename       text NOT NULL,
  source_format     text NOT NULL CHECK (source_format IN ('docx', 'pdf')),
  file_path         text,                                       -- relative to the applicant object-store root; NULL once deleted
  cv_raw_text       text,                                        -- NULL once deleted (privacy: nothing kept beyond the profile)
  profile           jsonb NOT NULL DEFAULT '{}',                 -- structured extraction result; see ajp.applicants.profile_extract
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  CHECK (jsonb_typeof(profile) = 'object')
);
CREATE INDEX applicant_created_idx ON applicant (created_at);
CREATE TRIGGER applicant_updated_at BEFORE UPDATE ON applicant
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- One row per (applicant, listing) the matching engine has scored - recomputed (never accumulated) each time
-- POST /v1/applicants/{id}/matches runs, so a stale score from before a profile edit or new listings never
-- lingers. factors carries the full, documented per-criterion breakdown (ajp.applicants.matching's own
-- MATCH_WEIGHTS) so a score is always explainable, never a black box (matches this project's own D4-style
-- transparency standard for the thesis similarity formula).
CREATE TABLE applicant_match (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  applicant_id   uuid NOT NULL REFERENCES applicant(id) ON DELETE CASCADE,
  listing_id     uuid NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
  score          numeric(5,2) NOT NULL CHECK (score BETWEEN 0 AND 100),
  factors        jsonb NOT NULL DEFAULT '{}',
  computed_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(factors) = 'object'),
  CONSTRAINT applicant_match_unique UNIQUE (applicant_id, listing_id)
);
CREATE INDEX applicant_match_applicant_idx ON applicant_match (applicant_id, score DESC);
