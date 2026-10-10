-- R1 (packet 005) run-level retry counter: transient failures are counted once per RUN; a URL/source is
-- marked blocked (and its org degraded to a deeplink) only on the 3rd consecutive failed run.
CREATE TABLE crawl_state (
  source_id            text NOT NULL,
  key                  text NOT NULL,                       -- board/feed URL
  consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  last_run_id          text,
  last_error           text,
  last_attempt_at      timestamptz,
  last_success_at      timestamptz,
  blocked_reason       text,                                -- NULL = crawlable
  history              jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(history) = 'array'),   -- last errors: [{run, ts, error}]
  updated_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, key)
);

-- per-organisation crawl configuration, e.g. {"max_pages": 3} for burst boards (izbori u zvanja season)
ALTER TABLE org_master ADD COLUMN crawl_config jsonb NOT NULL DEFAULT '{}'
  CHECK (jsonb_typeof(crawl_config) = 'object');

-- a deeplink may point at an organisation whose own site is still plain http
ALTER TABLE channel_link DROP CONSTRAINT channel_link_url_check;
ALTER TABLE channel_link ADD CONSTRAINT channel_link_url_check CHECK (url ~ '^https?://');
