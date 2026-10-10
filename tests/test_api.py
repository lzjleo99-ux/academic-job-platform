"""T14 acceptance: pytest API suite on seeded fixture DB exercising ALL 13 filters incl. unknown buckets and
salary EUR conversion with a fixed FX fixture; dedupe group test; pagination + sort; CSV export smoke;
OpenAPI renders; /health reflects a deliberately degraded source.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.channels import euraxess
from ajp.ingest import upsert_listing_deduped
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime.now(timezone.utc)


def add_source(conn, sid, kind="scrape_html"):
    conn.execute("INSERT INTO source (id, kind, base_url, robots_ok) VALUES (%s,%s,'https://x.invalid',true) "
                 "ON CONFLICT DO NOTHING", (sid, kind))


def add_org(conn, slug, **kw):
    kw.setdefault("country", "RS"); kw.setdefault("type", "university"); kw.setdefault("name_en", slug)
    cols = ", ".join(kw); ph = ", ".join(f"%({c})s" for c in kw)
    return conn.execute(f"INSERT INTO org_master (slug, {cols}) VALUES (%(slug)s, {ph}) RETURNING id",
                        dict(kw, slug=slug)).fetchone()[0]


def raw(store, sid, n=[0]):
    n[0] += 1
    return put_raw(store, kind="html", source_id=sid, url=f"https://x.invalid/{sid}/{n[0]}", content=b"x",
                   fetched_at=NOW, status=200, robots_allowed=True).key


@pytest.fixture()
def seeded(conn, tmp_path):
    store = LocalFsStore(tmp_path)
    for sid in ("rs_org_boards", "sljaka", "academictransfer", "nsz"):
        add_source(conn, sid)
    euraxess.seed(conn)  # channel + 2 links -> exercises /v1/channels
    org_rs = add_org(conn, "demo-uni-rs", name_en="Demo University", city="Novi Sad", homepage="https://demo-uni.invalid/")
    org_nl = add_org(conn, "demo-uni-nl", country="NL", name_en="Demo TU", city="Delft", homepage="https://demo-tu.invalid/")

    conn.execute("INSERT INTO fx_rate (rate_date, currency, per_eur, source) VALUES (CURRENT_DATE, 'GBP', 0.85, 'ECB')")
    # a deliberately degraded source for /health + /v1/sources
    conn.execute("""INSERT INTO crawl_state (source_id, key, consecutive_failures, blocked_reason, last_attempt_at)
                    VALUES ('sljaka','https://sljaka.invalid/feed', 3, 'unreachable_after_3_runs: timeout', now())""")

    def ingest(**kw):
        kw.setdefault("ext_uid", kw["url"]); kw.setdefault("raw_html_ref", raw(store, kw["source_id"]))
        return upsert_listing_deduped(conn, kw, store)

    # 1: RS PhD, direct org board, EUR/month salary, near-term deadline, coords near Novi Sad, will also be
    #    the canonical-by-trust-rank member of a 3-source merge (added below).
    shared_url = "https://demo-uni.invalid/jobs/phd-robotics"
    id1, action1 = ingest(source_id="rs_org_boards", url=shared_url, title_raw="PhD Position in Robotics",
                          country_iso2="RS", position_kind="phd", kind="employment", org_id=org_rs, org_raw="Demo University",
                          city="Novi Sad", lat=45.2671, lon=19.8335, salary_min=3000, salary_max=3000,
                          salary_currency="EUR", salary_period="month", salary_raw="3000 EUR/month",
                          deadline=NOW + timedelta(days=7), posted_at=NOW - timedelta(days=1),
                          duration_months_v=12, duration_months_raw="12 months", duration_months_conf=0.9,
                          ft_pt="full", foreigner_eligible="unknown", degree_required="phd",
                          language=[{"lang": "en", "level": "C1", "required": True}],
                          disciplines_euroscivoc=["1.2_computer_and_information_sciences"], contract="fixed")
    assert action1 == "inserted"
    # same posting, seen on 2 aggregators -> merges into id1; canonical must stay the direct-org entry
    for sid in ("academictransfer", "sljaka"):
        lid, action = ingest(source_id=sid, url=shared_url, title_raw="PhD Robotics (aggregator copy)",
                             country_iso2="RS", position_kind="phd", kind="employment", org_raw="Demo University")
        assert action == "merged" and lid == id1

    # 2: NL postdoc, hourly GBP (needs *40*52 annualization + fx), mid-range deadline
    ingest(source_id="academictransfer", url="https://demo-tu.invalid/jobs/postdoc-1", title_raw="Postdoc in Physics",
          country_iso2="NL", position_kind="postdoc", kind="employment", org_id=org_nl, org_raw="Demo TU", city="Delft",
          salary_min=25, salary_max=30, salary_currency="GBP", salary_period="hour", salary_raw="GBP 25-30/hour",
          deadline=NOW + timedelta(days=40), posted_at=NOW - timedelta(days=2), duration_months_v=24,
          duration_months_raw="24 months", duration_months_conf=0.8, ft_pt="full", foreigner_eligible="yes",
          degree_required="phd", language=[{"lang": "nl", "level": "B2", "required": False}],
          disciplines_euroscivoc=["2.1_physical_sciences"], contract="fixed")

    # 3: enrollment kind - hidden by default
    ingest(source_id="rs_org_boards", url="https://demo-uni.invalid/jobs/phd-call", title_raw="PhD Enrolment Call",
          country_iso2="RS", position_kind="phd", kind="enrollment", org_id=org_rs, org_raw="Demo University")

    # 4: internship
    ingest(source_id="nsz", url="https://nsz.invalid/jobs/intern-1", title_raw="Lab Internship",
          country_iso2="RS", position_kind="intern", kind="internship", org_id=org_rs, org_raw="Demo University")

    # 5: grant_call, no salary stated (unspecified bucket), foreigner=no
    ingest(source_id="nsz", url="https://nsz.invalid/jobs/grant-1", title_raw="Mobility Grant Call",
          country_iso2="RS", position_kind="researcher_title", kind="grant_call", org_id=org_rs, org_raw="Demo University",
          foreigner_eligible="no")

    # 6: permanent contract, no duration stated
    ingest(source_id="rs_org_boards", url="https://demo-uni.invalid/jobs/tech-1", title_raw="Lab Technician",
          country_iso2="RS", position_kind="technician", kind="employment", org_id=org_rs, org_raw="Demo University",
          contract="permanent")

    # 7: part-time -> study_while_working derives 'true' via ft_pt='part' even with study_while_work_ok_v unset
    ingest(source_id="rs_org_boards", url="https://demo-uni.invalid/jobs/ra-1", title_raw="Research Assistant (part-time)",
          country_iso2="RS", position_kind="research_assistant", kind="employment", org_id=org_rs, org_raw="Demo University",
          ft_pt="part")

    # 8: closed status - excluded by the default status=open filter
    ingest(source_id="rs_org_boards", url="https://demo-uni.invalid/jobs/closed-1", title_raw="Closed Position",
          country_iso2="RS", position_kind="phd", kind="employment", org_id=org_rs, org_raw="Demo University",
          status="closed")

    return {"org_rs": str(org_rs), "org_nl": str(org_nl), "canonical_id": str(id1), "shared_url": shared_url}


@pytest.fixture()
def client(conn, seeded):
    app = create_app()
    conn.row_factory = dict_row      # the API always reads rows as dicts; the shared `conn` fixture defaults to tuples

    def _override_get_conn():
        yield conn

    app.dependency_overrides[get_conn] = _override_get_conn
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- basic shape / defaults --------------------
def test_default_listing_view_shows_every_kind_but_still_hides_closed(client):
    """T33 (packet 018, Q6): PM's own real-DB audit found the old kind=employment-only default hid 1,280 of
    3,948 open listings (grant_call/internship/enrollment/other) from every fresh visitor - unfiltered by
    default now (every real kind), same as location_class already was (T23, PM ruling R2). status=open is
    still the one default filter that stays - a closed/expired listing is never a live opportunity."""
    r = client.get("/v1/listings", params={"page_size": 50})
    assert r.status_code == 200
    body = r.json()
    kinds = {x["kind"] for x in body["results"]}
    assert kinds == {"employment", "enrollment", "internship", "grant_call"}
    titles = {x["title_raw"] for x in body["results"]}
    assert "PhD Enrolment Call" in titles                              # enrollment is no longer hidden
    assert "Closed Position" not in titles                             # status=open default is unchanged
    assert body["page"]["total"] == len(body["results"])


def test_openapi_renders(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200 and r.json()["info"]["title"]
    assert client.get("/docs").status_code == 200


# ---------------------------------------------------------------- the 13 filters -----------------------------
def test_filter_country(client):
    r = client.get("/v1/listings", params={"country": "NL"})
    assert {x["country_iso2"] for x in r.json()["results"]} == {"NL"}


def test_filter_city_and_radius(client, seeded):
    r = client.get("/v1/listings", params={"city": "Novi Sad"})
    assert all(x["city"] == "Novi Sad" for x in r.json()["results"])
    # Belgrade is ~70km from Novi Sad's seeded coords
    near = client.get("/v1/listings", params={"lat": 44.7866, "lon": 20.4489, "r_km": 100, "kind": "employment"})
    assert any(x["id"] == seeded["canonical_id"] for x in near.json()["results"])
    far = client.get("/v1/listings", params={"lat": 44.7866, "lon": 20.4489, "r_km": 5, "kind": "employment", "country": "NL"})
    assert far.json()["results"] == []


def test_filter_discipline(client):
    r = client.get("/v1/listings", params={"discipline": "2.1_physical_sciences"})
    assert {x["position_kind"] for x in r.json()["results"]} == {"postdoc"}


def test_filter_foreigner_tristate_including_unknown(client):
    unknown = client.get("/v1/listings", params={"foreigner": "unknown", "kind": ["employment", "grant_call"]})
    assert any(x["title_raw"] == "PhD Position in Robotics" for x in unknown.json()["results"])
    no = client.get("/v1/listings", params={"foreigner": "no", "kind": "grant_call"})
    assert {x["foreigner_eligible"] for x in no.json()["results"]} == {"no"}


def test_filter_salary_range_and_bucket_and_eur_year_conversion(client):
    r = client.get("/v1/listings", params={"salary_min": 30000, "kind": "employment"})
    hits = {x["title_raw"]: x["salary_eur_year"] for x in r.json()["results"]}
    assert "PhD Position in Robotics" in hits and hits["PhD Position in Robotics"]["min_eur_year"] == 36000.0
    postdoc = hits["Postdoc in Physics"]
    # 25 GBP/hr * 40 * 52 = 52000 GBP/year, / 0.85 GBP-per-EUR = 61176.47 EUR/year
    assert postdoc["min_eur_year"] == pytest.approx(61176.47, abs=0.5)
    assert postdoc["fx_source"] == "ECB" and "40h/week" in postdoc["note"]
    bucketed = client.get("/v1/listings", params={"salary_bucket": "60_100k", "kind": "employment"})
    assert {x["title_raw"] for x in bucketed.json()["results"]} == {"Postdoc in Physics"}


def test_filter_salary_unspecified_toggle(client):
    excluded = client.get("/v1/listings", params={"salary_include_unspecified": False, "salary_min": 1, "kind": "grant_call"})
    assert excluded.json()["results"] == []                            # the grant_call has no stated salary
    included = client.get("/v1/listings", params={"salary_include_unspecified": True, "kind": "grant_call"})
    assert any(x["title_raw"] == "Mobility Grant Call" for x in included.json()["results"])


def test_filter_duration_bucket_including_permanent_and_unknown(client):
    permanent = client.get("/v1/listings", params={"duration_bucket": "permanent", "kind": "employment"})
    assert {x["title_raw"] for x in permanent.json()["results"]} == {"Lab Technician"}
    band = client.get("/v1/listings", params={"duration_bucket": "24_36", "kind": "employment"})
    assert {x["title_raw"] for x in band.json()["results"]} == {"Postdoc in Physics"}


def test_filter_ft_pt(client):
    r = client.get("/v1/listings", params={"ft_pt": "part", "kind": "employment"})
    assert {x["title_raw"] for x in r.json()["results"]} == {"Research Assistant (part-time)"}


def test_filter_study_while_working_derived_true_and_default_unknown_kept(client):
    true_only = client.get("/v1/listings", params={"study_while_working": "true", "kind": "employment"})
    assert {x["title_raw"] for x in true_only.json()["results"]} == {"Research Assistant (part-time)"}
    everyone = client.get("/v1/listings", params={"kind": "employment"})  # no filter -> unknowns not dropped
    assert len(everyone.json()["results"]) >= 4


def test_filter_kind_default_on_off_and_toggles(client):
    """T33 (packet 018, Q6): omitting kind entirely is unfiltered (every real kind); passing an explicit
    value - even a single one - narrows it, exactly as every other multi-valued filter here already works."""
    default = client.get("/v1/listings")
    assert {x["kind"] for x in default.json()["results"]} == {"employment", "enrollment", "internship", "grant_call"}
    enrollment = client.get("/v1/listings", params={"kind": "enrollment"})
    assert {x["title_raw"] for x in enrollment.json()["results"]} == {"PhD Enrolment Call"}
    multi = client.get("/v1/listings", params={"kind": ["employment", "internship"]})
    assert {"internship", "employment"} <= {x["kind"] for x in multi.json()["results"]}
    assert {x["kind"] for x in multi.json()["results"]} <= {"internship", "employment"}


def test_filter_position_kind(client):
    r = client.get("/v1/listings", params={"position_kind": "postdoc"})
    assert {x["title_raw"] for x in r.json()["results"]} == {"Postdoc in Physics"}


def test_filter_language_and_level(client):
    r = client.get("/v1/listings", params={"language": "nl"})
    assert {x["title_raw"] for x in r.json()["results"]} == {"Postdoc in Physics"}
    r2 = client.get("/v1/listings", params={"language": "nl", "language_level": "B2"})
    assert len(r2.json()["results"]) == 1
    r3 = client.get("/v1/listings", params={"language": "nl", "language_level": "C2"})
    assert r3.json()["results"] == []


def test_filter_degree_required_including_unknown(client):
    unknown = client.get("/v1/listings", params={"degree_required": "unknown", "kind": "employment"})
    titles = {x["title_raw"] for x in unknown.json()["results"]}
    assert "Lab Technician" in titles and "PhD Position in Robotics" not in titles


def test_filter_deadline_closing_soon_and_range_and_status_default_excludes_expired(client):
    soon = client.get("/v1/listings", params={"deadline_bucket": "closing_soon", "kind": "employment"})
    assert {x["title_raw"] for x in soon.json()["results"]} == {"PhD Position in Robotics"}
    ranged = client.get("/v1/listings", params={"deadline_from": str((NOW + timedelta(days=30)).date()), "kind": "employment"})
    assert {x["title_raw"] for x in ranged.json()["results"]} == {"Postdoc in Physics"}
    default = client.get("/v1/listings", params={"kind": "employment"})
    assert "Closed Position" not in {x["title_raw"] for x in default.json()["results"]}
    explicit = client.get("/v1/listings", params={"status": ["open", "closed"], "kind": "employment"})
    assert "Closed Position" in {x["title_raw"] for x in explicit.json()["results"]}


# ---------------------------------------------------------------- dedupe / canonical --------------------------
def test_dedupe_group_three_sources_canonical_is_direct_org(client, seeded):
    r = client.get(f"/v1/listings/{seeded['canonical_id']}")
    body = r.json()
    assert {s["source_id"] for s in body["sources"]} == {"rs_org_boards", "academictransfer", "sljaka"}
    assert body["canonical_source_id"] == "rs_org_boards" and body["verified_direct"] is True
    r2 = client.get("/v1/listings", params={"kind": "employment", "text": "Robotics"})
    assert r2.json()["page"]["total"] == 1                             # one row, not three


# ---------------------------------------------------------------- pagination / sort / export -------------------
def test_pagination_and_sort_correctness(client):
    r1 = client.get("/v1/listings", params={"kind": "employment", "page_size": 2, "page": 1, "sort": "posted_desc"})
    r2 = client.get("/v1/listings", params={"kind": "employment", "page_size": 2, "page": 2, "sort": "posted_desc"})
    ids1 = [x["id"] for x in r1.json()["results"]]
    ids2 = [x["id"] for x in r2.json()["results"]]
    assert not set(ids1) & set(ids2)                                   # no overlap between pages
    assert r1.json()["page"]["total"] == r1.json()["page"]["total"] and r1.json()["page"]["total_pages"] >= 2
    salary_sorted = client.get("/v1/listings", params={"kind": "employment", "sort": "salary_desc"}).json()["results"]
    amounts = [x["salary_eur_year"]["max_eur_year"] or -1 for x in salary_sorted]
    assert amounts == sorted(amounts, reverse=True)
    deadline_sorted = client.get("/v1/listings", params={"kind": "employment", "sort": "deadline_asc"}).json()["results"]
    deadlines = [x["deadline"] for x in deadline_sorted if x["deadline"]]
    assert deadlines == sorted(deadlines)


def test_csv_export_smoke(client):
    r = client.get("/v1/listings/export", params={"format": "csv", "kind": "employment"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("id,title,org")
    assert len(r.text.splitlines()) > 1
    j = client.get("/v1/listings/export", params={"format": "json", "kind": "employment"})
    assert isinstance(j.json(), list) and len(j.json()) > 0


def test_q6_pm_gate_unfiltered_landing_total_reconciles_exactly_with_the_kind_facet(client):
    """T33 (packet 018, Q6) - PM's own explicit gate: the unfiltered landing total (no kind param at all)
    must equal the sum of every bucket the kind facet itself reports, over the SAME unfiltered baseline -
    'other' rows are included in both, per PM's own explicit kind list for this default (employment,
    enrollment, internship, grant_call, other)."""
    landing = client.get("/v1/listings", params={"page_size": 1}).json()["page"]["total"]
    facets = client.get("/v1/facets").json()
    assert sum(b["count"] for b in facets["kind"]) == landing


# ---------------------------------------------------------------- facets, orgs, sources, meta, channels, health -
def test_facets_counts_and_unaffected_by_their_own_filter(client):
    r = client.get("/v1/facets", params={"kind": "employment"})
    body = r.json()
    assert {b["value"]: b["count"] for b in body["ft_pt"]}.get("full", 0) >= 2
    picked = client.get("/v1/facets", params={"kind": "employment", "ft_pt": "part"}).json()
    # ft_pt's own facet still lists 'full' with its full (non-collapsed) count despite ft_pt=part active
    assert any(b["value"] == "full" and b["count"] >= 2 for b in picked["ft_pt"])


def test_orgs_endpoints(client, seeded):
    listing = client.get("/v1/orgs", params={"country": "RS"}).json()
    assert any(o["id"] == seeded["org_rs"] for o in listing)
    detail = client.get(f"/v1/orgs/{seeded['org_rs']}").json()
    assert detail["name_en"] == "Demo University" and detail["open_listing_count"] >= 4
    assert client.get("/v1/orgs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_sources_endpoint_reports_the_degraded_source(client):
    body = client.get("/v1/sources").json()
    by_id = {s["id"]: s for s in body}
    assert by_id["sljaka"]["last_run_status"] == "degraded"
    assert by_id["sljaka"]["degraded_reasons"]
    assert by_id["rs_org_boards"]["route_type"] == "direct_org"
    assert by_id["academictransfer"]["route_type"] == "aggregator"
    assert by_id["euraxess"]["last_run_status"] == "not_applicable"


def test_meta_endpoint_lists_taxonomies_and_flags_the_discipline_gap(client):
    body = client.get("/v1/meta").json()
    assert body["disciplines_taxonomy_complete"] is False
    assert any(f["feature"] == "discipline_hierarchy" for f in body["degraded_features"])
    assert "phd" in body["position_kind"] and "unknown" in body["degree_required"]
    assert body["fx_as_of"] is not None and "GBP" in body["currencies"]


def test_channels_endpoint_lists_euraxess_never_proxied(client):
    body = client.get("/v1/channels").json()
    slugs = {c["slug"] for c in body}
    assert "serbia-jobs" in slugs
    assert all(c["url"].startswith("https://euraxess.ec.europa.eu") for c in body if c["source_id"] == "euraxess")


def test_health_reflects_the_degraded_source(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and "sljaka" in body["degraded_sources"]
    assert body["fx_currencies"] >= 1
