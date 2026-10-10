"""T23: pagination + query-param edge cases the T14 acceptance suite (test_api.py) doesn't already cover -
empty filters, an unknown enum value on a *constrained* param (422, never silently coerced), an unknown
value on an *unconstrained* list filter (200, empty results - never a guessed match either), null salary
rows surviving JSON serialization, and a huge/invalid page_size/page being rejected outright rather than
silently clamped. Also covers the T23 (PM ruling R2) location_class filter/facet end-to-end against a real
seeded null-country row, since test_api.py's own fixture pre-dates migration 0020 and has no such row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.ingest import upsert_listing_deduped
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime.now(timezone.utc)


def _add_source(conn, sid):
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES (%s,'scrape_html','https://x.invalid',true) "
                 "ON CONFLICT DO NOTHING", (sid,))


def _raw(store, sid, n=[0]):
    n[0] += 1
    return put_raw(store, kind="html", source_id=sid, url=f"https://x.invalid/{sid}/{n[0]}", content=b"x",
                   fetched_at=NOW, status=200, robots_allowed=True).key


@pytest.fixture()
def seeded(conn, tmp_path):
    store = LocalFsStore(tmp_path)
    _add_source(conn, "rs_org_boards")

    def ingest(**kw):
        kw.setdefault("ext_uid", kw["url"]); kw.setdefault("raw_html_ref", _raw(store, kw["source_id"]))
        return upsert_listing_deduped(conn, kw, store)

    # a normal, fully-located row (page-through/sort filler) - location_class set explicitly, exactly like a
    # real connector would (jobposting.classify_location), since ingest() here bypasses that entirely.
    for i in range(5):
        ingest(source_id="rs_org_boards", url=f"https://x.invalid/job-{i}", title_raw=f"Researcher {i}",
              country_iso2="RS", position_kind="researcher_title", kind="employment",
              location_class="located", posted_at=NOW - timedelta(days=i))

    # T23 (PM ruling R2): a real null-country row with each non-'located' location_class, plus a stated
    # salary=None row to exercise null-salary JSON serialization end-to-end.
    ingest(source_id="rs_org_boards", url="https://x.invalid/job-remote", title_raw="Fully Remote Data Scientist",
          country_iso2=None, position_kind="research_assistant", kind="employment",
          location_class="remote", location_class_conf=0.8, location_class_raw="fully remote")
    ingest(source_id="rs_org_boards", url="https://x.invalid/job-hybrid", title_raw="Hybrid Lab Assistant",
          country_iso2=None, position_kind="technician", kind="employment",
          location_class="hybrid", location_class_conf=0.8, location_class_raw="hybrid role")
    ingest(source_id="rs_org_boards", url="https://x.invalid/job-unknown", title_raw="Mystery Location Role",
          country_iso2=None, position_kind="other", kind="employment", location_class="unknown")
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


# ---------------------------------------------------------------- empty filters --------------------------------
def test_no_query_params_at_all_returns_the_default_view_not_an_error(client):
    r = client.get("/v1/listings")
    assert r.status_code == 200
    body = r.json()
    assert body["page"]["total"] >= 5                     # the 5 filler rows + 3 location_class rows, all employment/open
    assert {x["kind"] for x in body["results"]} == {"employment"}


def test_empty_string_query_param_is_a_real_no_match_value_not_a_crash(client):
    """FastAPI/Starlette parses ?country= (given, but with no value) as country=[''] for Optional[List[str]] -
    a real, if unusual, filter value ('' is never a real country_iso2), not the same as omitting the param
    entirely - it must be handled like any other unmatched value (200, empty results), never a 500."""
    r = client.get("/v1/listings", params={"country": ""})
    assert r.status_code == 200
    assert r.json()["results"] == []


# ---------------------------------------------------------------- unknown enum values ---------------------------
def test_unknown_value_on_a_constrained_enum_param_is_rejected_422(client):
    r = client.get("/v1/listings", params={"sort": "not_a_real_sort"})
    assert r.status_code == 422


def test_unknown_value_on_an_unconstrained_list_filter_yields_empty_not_an_error(client):
    """position_kind/ft_pt/etc. are never guessed into the nearest real value - a value nothing matches just
    yields zero rows, exactly like a real, valid-but-unused value would."""
    r = client.get("/v1/listings", params={"position_kind": "warlock", "kind": "employment"})
    assert r.status_code == 200 and r.json()["results"] == []
    r2 = client.get("/v1/listings", params={"location_class": "nowhere", "kind": "employment"})
    assert r2.status_code == 200 and r2.json()["results"] == []


def test_unknown_enum_value_in_a_facet_request_still_returns_200_and_collapses_other_dimensions(client):
    """The standard multi-select-facet rule (filters.py's own docstring): a dimension's own bucket list is
    computed with THAT dimension's filter excluded, so position_kind's own facet still lists every real
    bucket even while position_kind=warlock is active (proving the exclude= convention holds for an unknown
    value too) - but a DIFFERENT dimension's facet (ft_pt) has no such exclusion for position_kind, so an
    unmatched position_kind filter correctly collapses it to nothing, not a crash."""
    r = client.get("/v1/facets", params={"position_kind": "warlock", "kind": "employment"})
    assert r.status_code == 200
    assert {b["value"] for b in r.json()["position_kind"]} >= {"researcher_title"}
    assert r.json()["ft_pt"] == []


# ---------------------------------------------------------------- null salary survives serialization ------------
def test_a_row_with_no_stated_salary_serializes_with_null_fields_not_a_crash(client):
    r = client.get("/v1/listings", params={"kind": "employment", "text": "Researcher 0"})
    assert r.status_code == 200
    row = r.json()["results"][0]
    assert row["salary_eur_year"]["min_eur_year"] is None
    assert row["salary_eur_year"]["max_eur_year"] is None
    assert row["salary_currency"] is None


# ---------------------------------------------------------------- huge / invalid page params ---------------------
@pytest.mark.parametrize("page_size", [101, 1000, 1_000_000])
def test_a_page_size_over_the_cap_is_rejected_not_silently_clamped(client, page_size):
    r = client.get("/v1/listings", params={"page_size": page_size})
    assert r.status_code == 422


@pytest.mark.parametrize("page", [0, -1])
def test_a_non_positive_page_number_is_rejected(client, page):
    r = client.get("/v1/listings", params={"page": page})
    assert r.status_code == 422


def test_a_page_number_past_the_last_page_returns_an_empty_results_list_not_an_error(client):
    r = client.get("/v1/listings", params={"kind": "employment", "page": 9999, "page_size": 20})
    assert r.status_code == 200
    assert r.json()["results"] == []
    assert r.json()["page"]["total"] >= 5


def test_export_limit_over_its_own_cap_is_rejected(client):
    r = client.get("/v1/listings/export", params={"limit": 5001})
    assert r.status_code == 422


# ---------------------------------------------------------------- T23 (PM ruling R2): location_class -------------
def test_location_class_filter_selects_exactly_the_null_country_rows_of_that_class(client):
    remote = client.get("/v1/listings", params={"location_class": "remote", "kind": "employment"})
    assert {x["title_raw"] for x in remote.json()["results"]} == {"Fully Remote Data Scientist"}
    row = remote.json()["results"][0]
    assert row["country_iso2"] is None
    assert row["location_class_raw"] == "fully remote"

    hybrid = client.get("/v1/listings", params={"location_class": "hybrid", "kind": "employment"})
    assert {x["title_raw"] for x in hybrid.json()["results"]} == {"Hybrid Lab Assistant"}

    unknown = client.get("/v1/listings", params={"location_class": "unknown", "kind": "employment"})
    assert {x["title_raw"] for x in unknown.json()["results"]} == {"Mystery Location Role"}

    located = client.get("/v1/listings", params={"location_class": "located", "kind": "employment"})
    titles = {x["title_raw"] for x in located.json()["results"]}
    assert "Researcher 0" in titles and "Fully Remote Data Scientist" not in titles


def test_location_class_facet_reports_every_bucket_and_is_unaffected_by_its_own_filter(client):
    r = client.get("/v1/facets", params={"kind": "employment"})
    buckets = {b["value"]: b["count"] for b in r.json()["location_class"]}
    assert buckets.get("located", 0) >= 5
    assert buckets.get("remote", 0) == 1 and buckets.get("hybrid", 0) == 1 and buckets.get("unknown", 0) == 1
    # picking location_class=remote must not collapse the facet's own 'located' count to zero
    picked = client.get("/v1/facets", params={"kind": "employment", "location_class": "remote"})
    picked_buckets = {b["value"]: b["count"] for b in picked.json()["location_class"]}
    assert picked_buckets.get("located", 0) >= 5


def test_a_null_country_row_serializes_with_null_country_iso2_not_a_validation_error(client):
    r = client.get("/v1/listings", params={"kind": "employment", "text": "Mystery"})
    assert r.status_code == 200
    row = r.json()["results"][0]
    assert row["country_iso2"] is None
    assert row["location_class"] == "unknown"
