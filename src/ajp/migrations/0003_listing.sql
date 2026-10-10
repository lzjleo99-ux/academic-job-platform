-- data_contract.listing
-- Nested contract objects are flattened so filters can index them:
--   duration_months {v,conf,raw}      -> duration_months_v/_conf/_raw
--   study_while_work_ok {v,conf,raw}  -> study_while_work_ok_v/_conf/_raw
--   salary {min,max,currency,period,gross_net,conf,raw} -> salary_*
--   contact {emails,phones,name,scope} -> contact_*
--   lat_lon -> lat, lon
-- D6 (never fabricate): every extracted value must carry its raw excerpt; absence is NULL.
CREATE TABLE listing (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ext_uid       text NOT NULL,                              -- id at source
  source_id     text NOT NULL REFERENCES source(id),
  url           text NOT NULL,
  url_canon     text NOT NULL,                              -- dedupe: canonicalized url
  title_raw     text NOT NULL,
  title_norm_hash text,                                     -- dedupe: normalized title hash
  org_id        uuid REFERENCES org_master(id) ON DELETE SET NULL,
  org_raw       text,
  country_iso2  text NOT NULL CHECK (country_iso2 ~ '^[A-Z]{2}$'),
  city          text,
  lat           double precision CHECK (lat BETWEEN -90 AND 90),
  lon           double precision CHECK (lon BETWEEN -180 AND 180),
  disciplines_euroscivoc text[] NOT NULL DEFAULT '{}',
  position_kind text NOT NULL CHECK (position_kind IN
                  ('phd','postdoc','research_assistant','researcher_title','professor',
                   'lecturer','technician','admin','intern','other')),
  rs_research_title text CHECK (rs_research_title IN
                  ('istrazivac_pripravnik','istrazivac_saradnik','naucni_saradnik',
                   'visi_naucni_saradnik','naucni_savetnik')),
  contract      text CHECK (contract IN
                  ('fixed','permanent','grant','fellowship','phd_enrolment','service_contract')),
  ft_pt         text NOT NULL DEFAULT 'unknown' CHECK (ft_pt IN ('full','part','both','unknown')),

  duration_months_v    numeric(6,2) CHECK (duration_months_v > 0),
  duration_months_conf real NOT NULL DEFAULT 0 CHECK (duration_months_conf BETWEEN 0 AND 1),
  duration_months_raw  text,
  hours_pct     numeric(5,2) CHECK (hours_pct > 0 AND hours_pct <= 100),
  study_while_work_ok_v    boolean,
  study_while_work_ok_conf real NOT NULL DEFAULT 0 CHECK (study_while_work_ok_conf BETWEEN 0 AND 1),
  study_while_work_ok_raw  text,

  salary_min    numeric(14,2) CHECK (salary_min >= 0),
  salary_max    numeric(14,2) CHECK (salary_max >= 0),
  salary_currency text CHECK (salary_currency ~ '^[A-Z]{3}$'),
  salary_period text CHECK (salary_period IN ('month','year','hour','stipend')),
  salary_gross_net text CHECK (salary_gross_net IN ('gross','net','unknown')),
  salary_conf   real NOT NULL DEFAULT 0 CHECK (salary_conf BETWEEN 0 AND 1),
  salary_raw    text,
  -- dedupe: salary signature (NULL when the ad states no amount)
  salary_sig    text GENERATED ALWAYS AS (
                  CASE WHEN salary_min IS NULL AND salary_max IS NULL THEN NULL
                       ELSE coalesce(salary_min::text, '') || '|' || coalesce(salary_max::text, '') || '|'
                            || coalesce(salary_currency, '') || '|' || coalesce(salary_period, '')
                  END) STORED,

  degree_required text CHECK (degree_required IN ('bsc','msc','phd','none','unknown')),
  language      jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(language) = 'array'),
                -- [{"lang":"en","level":"raw string","required":true}]
  foreigner_eligible text NOT NULL DEFAULT 'unknown' CHECK (foreigner_eligible IN ('yes','no','unknown')),
  requirements_raw text,

  contact_emails text[] NOT NULL DEFAULT '{}',
  contact_phones text[] NOT NULL DEFAULT '{}',
  contact_name  text,
  contact_scope text CHECK (contact_scope IN ('ad','org_hr')),

  apply_url     text,
  deadline      timestamptz,
  posted_at     timestamptz,
  ad_lang       text,                                       -- sr-Latn | sr-Cyrl | en | ...
  raw_text      text,
  raw_html_ref  text,                                       -- object-store key (see ajp.rawstore)
  first_seen    timestamptz NOT NULL DEFAULT now(),
  last_seen     timestamptz NOT NULL DEFAULT now(),
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed','expired')),
  updated_at    timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT listing_lat_lon_together CHECK ((lat IS NULL) = (lon IS NULL)),
  CONSTRAINT listing_seen_order CHECK (last_seen >= first_seen),
  CONSTRAINT listing_duration_has_raw CHECK
    (duration_months_v IS NULL OR nullif(btrim(duration_months_raw), '') IS NOT NULL),
  CONSTRAINT listing_swok_has_raw CHECK
    (study_while_work_ok_v IS NULL OR nullif(btrim(study_while_work_ok_raw), '') IS NOT NULL),
  CONSTRAINT listing_salary_has_raw CHECK
    ((salary_min IS NULL AND salary_max IS NULL) OR nullif(btrim(salary_raw), '') IS NOT NULL),
  CONSTRAINT listing_salary_has_currency CHECK
    ((salary_min IS NULL AND salary_max IS NULL) OR salary_currency IS NOT NULL),
  CONSTRAINT listing_salary_range CHECK
    (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max),
  CONSTRAINT listing_source_ext_uid_key UNIQUE (source_id, ext_uid)
);

-- dedupe keys (contract): block by org_id + normalized title hash;
-- fuzzy candidates share deadline/salary signature; cross-source via url_canon (+ ext_uid above).
CREATE INDEX listing_block_idx      ON listing (org_id, title_norm_hash);
CREATE INDEX listing_url_canon_idx  ON listing (url_canon);
CREATE INDEX listing_fuzzy_sig_idx  ON listing (deadline, salary_sig);

-- filter support (filters_ui_spec)
CREATE INDEX listing_country_status_idx ON listing (country_iso2, status);
CREATE INDEX listing_open_deadline_idx  ON listing (deadline) WHERE status = 'open';
CREATE INDEX listing_position_kind_idx  ON listing (position_kind);
CREATE INDEX listing_disciplines_idx    ON listing USING gin (disciplines_euroscivoc);
CREATE INDEX listing_last_seen_idx      ON listing (source_id, last_seen);

CREATE TRIGGER listing_updated_at BEFORE UPDATE ON listing
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
