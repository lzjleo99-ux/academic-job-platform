-- T13: the same position can appear on multiple tier-1 aggregators (Nature + jobs.ac.uk + the employer's own
-- board). 'sources' records every place a listing was SEEN, on top of the one canonical row that already
-- exists (source_id/ext_uid stay the FIRST source that found it - never rewritten by a later merge).
-- Each element: {"source": source_id, "url": the source's own URL for it, "first_seen": ISO timestamp}.
ALTER TABLE listing ADD COLUMN sources jsonb NOT NULL DEFAULT '[]'
  CHECK (jsonb_typeof(sources) = 'array');
