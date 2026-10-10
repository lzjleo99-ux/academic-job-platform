-- data_contract.source
CREATE TABLE source (
  id            text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_.-]*$'),
  kind          text NOT NULL CHECK (kind IN ('api','feed','scrape_html','scrape_js','manual')),
  base_url      text NOT NULL,
  list_url      text,
  robots_ok     boolean,                       -- NULL = not yet checked
  rate_rps      numeric(6,3) NOT NULL DEFAULT 1.0 CHECK (rate_rps > 0),
  needs_browser boolean NOT NULL DEFAULT false,
  geo_scope     text,
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER source_updated_at BEFORE UPDATE ON source
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- data_contract.org_master
-- Additions beyond the contract: slug (stable seed key), parent_org_id (faculty -> university),
-- *_rank_raw (QS/THE publish bands such as '801-850'; *_rank holds the lower bound),
-- source_url (provenance, T4), notes (TODOs instead of invented values).
CREATE TABLE org_master (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug           text UNIQUE,
  name_native    text,
  name_en        text,
  country        text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
  city           text,
  homepage       text,
  career_urls    text[] NOT NULL DEFAULT '{}',
  contact_email  text,
  contact_phone  text,
  type           text NOT NULL CHECK (type IN
                   ('university','faculty','institute','lab','company','funding_body')),
  parent_org_id  uuid REFERENCES org_master(id) ON DELETE SET NULL,
  qs_rank        integer CHECK (qs_rank > 0),
  qs_rank_raw    text,
  the_rank       integer CHECK (the_rank > 0),
  the_rank_raw   text,
  openalex_id    text UNIQUE,
  accredited_rs  boolean,                      -- NULL = unknown / not applicable
  source_url     text,
  notes          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT org_has_a_name CHECK (name_native IS NOT NULL OR name_en IS NOT NULL)
);
CREATE INDEX org_master_country_type_idx ON org_master (country, type);
CREATE INDEX org_master_parent_idx ON org_master (parent_org_id);
CREATE TRIGGER org_master_updated_at BEFORE UPDATE ON org_master
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
