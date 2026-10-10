-- T21: one row per (source, scheduled run) - the scheduler's own evidence trail, distinct from crawl_state
-- (0009, per source+key retry/blocked-reason counters carried ACROSS runs). run_ledger is what a run
-- actually did THIS TIME: pages/rows counters come from the real connector's own return objects (its
-- report dict's counters, never estimated or re-derived after the fact - ajp.scheduler's own contract).
CREATE TABLE run_ledger (
  run_id         bigserial PRIMARY KEY,
  source_id      text NOT NULL REFERENCES source(id) ON DELETE CASCADE,
  started_at     timestamptz NOT NULL,
  finished_at    timestamptz,                              -- NULL while a run is still in flight
  status         text CHECK (status IN ('ok', 'error', 'blocked')),   -- NULL while in flight
  pages_fetched  integer CHECK (pages_fetched >= 0),
  rows_new       integer CHECK (rows_new >= 0),
  rows_updated   integer CHECK (rows_updated >= 0),
  blocked        jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(blocked) = 'object'),
  http_counters  jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(http_counters) = 'object'),
  note           text
);
CREATE INDEX run_ledger_source_started_idx ON run_ledger (source_id, started_at DESC);
