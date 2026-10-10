-- packet 004: listing kind taxonomy. Only 'employment' is shown in the default job search; anything
-- not positively classified stays 'other' (hidden by default), never guessed into 'employment'.
ALTER TABLE listing ADD COLUMN kind text NOT NULL DEFAULT 'other'
  CHECK (kind IN ('employment','enrollment','grant','internship','other'));
CREATE INDEX listing_kind_status_idx ON listing (kind, status);

-- crawl metadata for org_master (packet 004, T5 acceptance)
ALTER TABLE org_master
  ADD COLUMN host             text,
  ADD COLUMN render_mode      text CHECK (render_mode IN ('httpx','playwright','deeplink_only','skip')),
  ADD COLUMN robots_status    text CHECK (robots_status IN
                                ('allowed','allowed_no_robots','disallowed','unavailable')),
  ADD COLUMN robots_checked_at timestamptz;
CREATE INDEX org_master_render_idx ON org_master (render_mode);
