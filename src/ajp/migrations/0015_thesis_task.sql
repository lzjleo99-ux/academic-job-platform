-- T16/T17: one row per uploaded document. The file itself lives under data/thesis/{id}/ (rawstore-style
-- local object storage, never S3/remote by default - packet 008's privacy default); this table is task/report
-- metadata only. original_filename is kept here for the uploader's own UI (never printed to a log - see
-- ajp.thesis.store's own docstring); status lets /v1/thesis/{id} report progress even though this project's
-- processing is currently synchronous (a 'processing' row is never observed by a client, but the column
-- exists so a future async worker is a behavioural change, not a schema one).
CREATE TABLE thesis_task (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status            text NOT NULL DEFAULT 'done' CHECK (status IN ('processing', 'done', 'failed')),
  error             text,
  original_filename text NOT NULL,
  source_format     text NOT NULL CHECK (source_format IN ('docx', 'pdf')),
  template_name     text NOT NULL,
  file_path         text NOT NULL,                              -- relative to the thesis object-store root
  report            jsonb,                                      -- T16: serialized CheckReport
  similarity        jsonb,                                      -- T17: serialized SimilarityReport, NULL until run
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX thesis_task_created_idx ON thesis_task (created_at);
CREATE TRIGGER thesis_task_updated_at BEFORE UPDATE ON thesis_task
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
