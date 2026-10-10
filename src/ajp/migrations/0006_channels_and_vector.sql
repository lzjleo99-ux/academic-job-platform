-- pgvector: enabled when the server offers it. Vector columns / embeddings are deferred to M7.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
    CREATE EXTENSION IF NOT EXISTS vector;
  END IF;
END $$;

-- 'channel' = external search channel: we only publish deep links that open on the other site
-- in the END USER's browser. Nothing is fetched server-side and no listing rows exist for it.
ALTER TABLE source DROP CONSTRAINT source_kind_check;
ALTER TABLE source ADD CONSTRAINT source_kind_check
  CHECK (kind IN ('api','feed','scrape_html','scrape_js','manual','channel'));

CREATE TABLE channel_link (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id   text NOT NULL REFERENCES source(id) ON DELETE CASCADE,
  slug        text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9_-]*$'),
  label       text NOT NULL,
  url         text NOT NULL CHECK (url ~ '^https://'),
  country_iso2 text CHECK (country_iso2 ~ '^[A-Z]{2}$'),
  description text,
  opens_externally boolean NOT NULL DEFAULT true CHECK (opens_externally),  -- UI: "opens on <source>"
  pm_supplied boolean NOT NULL DEFAULT true,       -- curated by the PM; never scraped from the site
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, slug)
);

CREATE FUNCTION channel_link_requires_channel() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT kind FROM source WHERE id = NEW.source_id) IS DISTINCT FROM 'channel' THEN
    RAISE EXCEPTION 'channel_link.source_id % is not a channel source', NEW.source_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER channel_link_requires_channel BEFORE INSERT OR UPDATE ON channel_link
  FOR EACH ROW EXECUTE FUNCTION channel_link_requires_channel();

CREATE FUNCTION listing_rejects_channel_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT kind FROM source WHERE id = NEW.source_id) = 'channel' THEN
    RAISE EXCEPTION 'source % is a deeplink channel: it has no listing rows', NEW.source_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER listing_rejects_channel_source BEFORE INSERT OR UPDATE OF source_id ON listing
  FOR EACH ROW EXECUTE FUNCTION listing_rejects_channel_source();
