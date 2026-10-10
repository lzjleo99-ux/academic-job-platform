-- T20: state machine for a discovered career-path/board candidate - discover_career_paths() (discovery.py)
-- only ever writes 'discovered' rows; promotion to dry_run/active is a separate, deliberate decision (never
-- auto-activated on a guess), matching the standing rule that discovery alone must never enable ingestion.
CREATE TABLE discovery_state (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_slug        text NOT NULL REFERENCES org_master(slug) ON DELETE CASCADE,
  url             text NOT NULL,
  method          text NOT NULL CHECK (method IN ('href_harvest', 'fixed_path_backfill')),
  ats_family      text,                                  -- ats_detect.py's own finding on this URL, if any
  ats_tenant      text,
  state           text NOT NULL DEFAULT 'discovered' CHECK (state IN ('discovered', 'dry_run', 'active')),
  dry_run_rows    integer CHECK (dry_run_rows >= 0),      -- schema-valid fixture rows produced by a dry run
  promoted_by     text,                                   -- who/what promoted this row past 'discovered'
  promoted_at     timestamptz,
  discovered_at   timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_slug, url)
);
CREATE INDEX discovery_state_state_idx ON discovery_state (state);
CREATE TRIGGER discovery_state_updated_at BEFORE UPDATE ON discovery_state
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Every discover_career_paths() CALL (not just its hits) - the governance dashboard's own source of "per-host
-- requests", "discovery status" and honest attempted/found counts. discovery_state above only ever holds
-- found_urls; this log is what lets a 'this org was probed and found nothing' run be told apart from 'this
-- org has never been probed at all' (both are absent from discovery_state, but only one has a log row here).
CREATE TABLE discovery_run_log (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_slug       text NOT NULL,
  homepage_host  text NOT NULL,
  method         text NOT NULL,                        -- 'href_harvest' | 'fixed_path_backfill' | 'homepage_unreachable' | 'none'
  paths_fetched  integer NOT NULL DEFAULT 0 CHECK (paths_fetched >= 0),
  found_count    integer NOT NULL DEFAULT 0 CHECK (found_count >= 0),
  error          text,
  run_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX discovery_run_log_host_run_at_idx ON discovery_run_log (homepage_host, run_at);
