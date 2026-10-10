"""T23: a deterministic snapshot of every ajp.api.salary EUR/year conversion rule (packet 007's own table),
including the two 'cannot compute, never guessed' degraded cases packet 007 names explicitly: no FX rate on
file for a stated non-EUR currency, and a stipend with no contract duration to annualize over. test_api.py's
own fixture already exercises the happy-path GBP/hour conversion end-to-end (with a real fx_rate row) - this
file is the deterministic complement, one listing per rule, so a future change to salary.py's arithmetic or
rows.py's own note text breaks exactly the row it changed, not a distant, unrelated assertion.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.ingest import upsert_listing_deduped
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime.now(timezone.utc)


def _raw(store, sid, n=[0]):
    n[0] += 1
    return put_raw(store, kind="html", source_id=sid, url=f"https://x.invalid/{sid}/{n[0]}", content=b"x",
                   fetched_at=NOW, status=200, robots_allowed=True).key


@pytest.fixture()
def seeded(conn, tmp_path):
    store = LocalFsStore(tmp_path)
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('rs_org_boards','scrape_html',"
                "'https://x.invalid',true) ON CONFLICT DO NOTHING")
    # a real fx rate for SEK only - RSD/USD deliberately have NONE, to exercise the degraded 'no rate on
    # file' case against a real (missing) join, not a mocked one.
    conn.execute("INSERT INTO fx_rate (rate_date, currency, per_eur, source) VALUES (CURRENT_DATE, 'SEK', 11.5, 'ECB')")

    def ingest(url, **kw):
        kw.setdefault("ext_uid", url); kw.setdefault("raw_html_ref", _raw(store, "rs_org_boards"))
        kw.setdefault("source_id", "rs_org_boards"); kw.setdefault("country_iso2", "RS")
        kw.setdefault("position_kind", "researcher_title"); kw.setdefault("kind", "employment")
        kw.setdefault("location_class", "located")
        return upsert_listing_deduped(conn, dict(kw, url=url), store)

    ingest("https://x.invalid/eur-year", title_raw="EUR Year Direct",
          salary_min=40000, salary_max=50000, salary_currency="EUR", salary_period="year",
          salary_raw="40000-50000 EUR/year")
    ingest("https://x.invalid/eur-month", title_raw="EUR Month Times Twelve",
          salary_min=3000, salary_max=3500, salary_currency="EUR", salary_period="month",
          salary_raw="3000-3500 EUR/month")
    ingest("https://x.invalid/sek-hour", title_raw="SEK Hourly With A Real FX Rate",
          salary_min=200, salary_max=250, salary_currency="SEK", salary_period="hour",
          salary_raw="200-250 SEK/hour")
    ingest("https://x.invalid/rsd-nofx", title_raw="RSD Stated But No FX Rate On File",
          salary_min=200000, salary_max=250000, salary_currency="RSD", salary_period="month",
          salary_raw="200000-250000 RSD/month")
    ingest("https://x.invalid/stipend-with-duration", title_raw="Stipend Annualized Over Its Own Duration",
          salary_min=1200, salary_max=1200, salary_currency="EUR", salary_period="stipend",
          salary_raw="1200 EUR/stipend",
          duration_months_v=6, duration_months_raw="6 months", duration_months_conf=0.9)
    ingest("https://x.invalid/stipend-no-duration", title_raw="Stipend With No Duration To Annualize Over",
          salary_min=1200, salary_max=1200, salary_currency="EUR", salary_period="stipend",
          salary_raw="1200 EUR/stipend")
    return {}


@pytest.fixture()
def client(conn, seeded):
    app = create_app()
    conn.row_factory = dict_row

    def _override_get_conn():
        yield conn

    app.dependency_overrides[get_conn] = _override_get_conn
    with TestClient(app) as c:
        yield c


def _card(client, text):
    r = client.get("/v1/listings", params={"kind": "employment", "text": text})
    results = r.json()["results"]
    assert len(results) == 1, f"expected exactly one match for {text!r}, got {len(results)}"
    return results[0]


def test_eur_year_passes_through_unconverted(client):
    row = _card(client, "EUR Year Direct")
    basis = row["salary_eur_year"]
    assert basis["min_eur_year"] == 40000.0 and basis["max_eur_year"] == 50000.0
    assert basis["note"] is None


def test_eur_month_is_multiplied_by_twelve(client):
    row = _card(client, "EUR Month Times Twelve")
    basis = row["salary_eur_year"]
    assert basis["min_eur_year"] == 36000.0 and basis["max_eur_year"] == 42000.0


def test_sek_hourly_annualizes_then_converts_via_a_real_fx_rate(client):
    row = _card(client, "SEK Hourly With A Real FX Rate")
    basis = row["salary_eur_year"]
    # 200 SEK/hr * 40 * 52 = 416000 SEK/year, / 11.5 SEK-per-EUR
    assert basis["min_eur_year"] == pytest.approx(36173.91, abs=0.5)
    assert basis["fx_source"] == "ECB" and basis["fx_rate_date"] is not None
    assert "40h/week" in basis["note"]


def test_a_currency_with_no_fx_rate_on_file_degrades_to_null_never_a_guessed_rate(client):
    """packet 007's own degraded case: RSD is stated but no fx_rate row for it exists - EUR/year must stay
    null (never assumed 1:1 or any other guessed rate), with an explicit note explaining why."""
    row = _card(client, "RSD Stated But No FX Rate On File")
    basis = row["salary_eur_year"]
    assert basis["min_eur_year"] is None and basis["max_eur_year"] is None
    assert basis["fx_source"] is None and basis["fx_rate_date"] is None
    assert "no FX rate on file for RSD" in basis["note"]
    # the raw, ad-stated figures are never lost just because they can't be converted (D6) - the card itself
    # doesn't carry salary_min/currency (only the detail view does), so check there.
    detail = client.get(f"/v1/listings/{row['id']}").json()
    assert detail["salary_min"] == 200000 and detail["salary_currency"] == "RSD"


def test_stipend_with_a_stated_duration_annualizes_over_it(client):
    row = _card(client, "Stipend Annualized Over Its Own Duration")
    basis = row["salary_eur_year"]
    # 1200 EUR / 6 months * 12 = 2400 EUR/year
    assert basis["min_eur_year"] == 2400.0 and basis["max_eur_year"] == 2400.0
    assert "annualized over the ad's own stated duration" in basis["note"]


def test_stipend_with_no_duration_degrades_to_null_never_a_guessed_duration(client):
    row = _card(client, "Stipend With No Duration To Annualize Over")
    basis = row["salary_eur_year"]
    assert basis["min_eur_year"] is None and basis["max_eur_year"] is None
    assert "no contract duration" in basis["note"]


def test_salary_bucket_facet_places_every_row_including_the_two_degraded_ones(client):
    r = client.get("/v1/facets", params={"kind": "employment"})
    buckets = {b["value"]: b["count"] for b in r.json()["salary_bucket"]}
    # the two rows whose EUR/year could not be computed at all land in 'unspecified', not silently omitted
    assert buckets.get("unspecified", 0) >= 2
    assert sum(buckets.values()) == 6                        # every one of the 6 seeded rows lands somewhere
