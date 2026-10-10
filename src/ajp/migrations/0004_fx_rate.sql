-- FX snapshots for salary normalization to EUR.
-- per_eur = units of `currency` per 1 EUR (ECB quoting convention).
-- ECB does not publish RSD; NBS / ERAPI rows supply it under their own `source` (packet 002).
CREATE TABLE fx_rate (
  rate_date  date NOT NULL,
  currency   text NOT NULL CHECK (currency ~ '^[A-Z]{3}$' AND currency <> 'EUR'),
  per_eur    numeric(20,8) NOT NULL CHECK (per_eur > 0),
  source     text NOT NULL CHECK (source ~ '^[A-Z0-9_]+$'),
  fetched_at timestamptz NOT NULL DEFAULT now(),
  raw_ref    text,                                          -- object-store key of the raw snapshot
  PRIMARY KEY (rate_date, currency, source)
);
CREATE INDEX fx_rate_currency_date_idx ON fx_rate (currency, rate_date DESC);

-- Source precedence NBS > ECB > ERAPI (NBS = National Bank of Serbia official middle rate,
-- ERAPI = open.er-api.com, indicative only). Unknown sources rank last.
CREATE FUNCTION fx_source_rank(s text) RETURNS integer
LANGUAGE sql IMMUTABLE AS
$$ SELECT CASE s WHEN 'NBS' THEN 1 WHEN 'ECB' THEN 2 WHEN 'ERAPI' THEN 3 ELSE 9 END $$;

-- One rate per currency: the highest-precedence source among those within 7 days of the newest
-- rate for that currency (so a source that stopped publishing loses to a fresher one, and a
-- weekend gap in ECB does not demote it below ERAPI). rate_date/source are exposed so that
-- consumers can record which source converted an amount.
CREATE VIEW fx_rate_latest AS
SELECT DISTINCT ON (currency) currency, rate_date, per_eur, source
FROM (SELECT *, max(rate_date) OVER (PARTITION BY currency) AS newest FROM fx_rate) t
WHERE rate_date >= newest - 7
ORDER BY currency, fx_source_rank(source), rate_date DESC;

-- Salary in EUR per year, computed only from what the ad states:
--   period 'year' -> x1, 'month' -> x12; 'hour' and 'stipend' stay NULL (would need an
--   assumed hours/year or an unstated payment period); currency without a rate stays NULL.
CREATE VIEW listing_salary_eur_year AS
SELECT l.id AS listing_id,
       l.salary_currency,
       l.salary_period,
       l.salary_gross_net,
       r.rate_date AS fx_rate_date,
       r.source    AS fx_source,
       round(l.salary_min * f.factor / f.per_eur, 2) AS salary_min_eur_year,
       round(l.salary_max * f.factor / f.per_eur, 2) AS salary_max_eur_year
FROM listing l
CROSS JOIN LATERAL (SELECT CASE l.salary_period WHEN 'year' THEN 1 WHEN 'month' THEN 12 END AS factor) af
LEFT JOIN fx_rate_latest r ON r.currency = l.salary_currency
CROSS JOIN LATERAL (SELECT af.factor,
                           CASE WHEN l.salary_currency = 'EUR' THEN 1::numeric ELSE r.per_eur END AS per_eur) f
WHERE l.salary_min IS NOT NULL OR l.salary_max IS NOT NULL;
