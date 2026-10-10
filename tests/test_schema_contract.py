"""The data_contract rules enforced in the database (D6: never fabricate)."""
import psycopg
import pytest
from psycopg import errors


@pytest.fixture()
def src(conn):
    conn.execute("INSERT INTO source (id, kind, base_url) VALUES ('euraxess','scrape_js','https://euraxess.ec.europa.eu')")
    return "euraxess"


def add(conn, **kw):
    row = dict(ext_uid="1", source_id="euraxess", url="https://x.test/1", url_canon="https://x.test/1",
               title_raw="PhD position", country_iso2="RS", position_kind="phd")
    row.update(kw)
    cols = ", ".join(row)
    ph = ", ".join(f"%({k})s" for k in row)
    return conn.execute(f"INSERT INTO listing ({cols}) VALUES ({ph}) RETURNING id", row).fetchone()[0]


def test_minimal_row_all_optionals_null(conn, src):
    lid = add(conn)
    r = conn.execute("""SELECT duration_months_v, salary_min, salary_sig, degree_required, contract, deadline,
                               foreigner_eligible, ft_pt, status, language, contact_emails
                        FROM listing WHERE id=%s""", (lid,)).fetchone()
    assert r == (None, None, None, None, None, None, "unknown", "unknown", "open", [], [])


@pytest.mark.parametrize("cols", [
    {"duration_months_v": 36},
    {"study_while_work_ok_v": True},
    {"salary_min": 1000, "salary_currency": "EUR"},
    {"duration_months_v": 36, "duration_months_raw": "   "},
])
def test_value_without_raw_excerpt_rejected(conn, src, cols):
    with pytest.raises(errors.CheckViolation):
        add(conn, **cols)


def test_value_with_raw_excerpt_accepted(conn, src):
    add(conn, duration_months_v=36, duration_months_raw="3 years", duration_months_conf=0.9)


@pytest.mark.parametrize("cols", [
    {"duration_months_conf": 1.5}, {"salary_conf": -0.1}, {"study_while_work_ok_conf": 2},
    {"salary_min": 5, "salary_raw": "x"},                                     # amount but no currency
    {"salary_min": 9, "salary_max": 5, "salary_currency": "EUR", "salary_raw": "x"},
    {"lat": 44.8}, {"lat": 95, "lon": 20}, {"country_iso2": "srb"}, {"position_kind": "wizard"},
    {"foreigner_eligible": "maybe"}, {"salary_period": "week"}, {"status": "gone"},
    {"rs_research_title": "docent"}, {"contact_scope": "public"}, {"hours_pct": 0}, {"hours_pct": 120},
])
def test_invalid_values_rejected(conn, src, cols):
    with pytest.raises(errors.CheckViolation):
        add(conn, **cols)


def test_position_kind_is_required(conn, src):
    with pytest.raises(errors.NotNullViolation):
        conn.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2)
                        VALUES ('1','euraxess','u','u','t','RS')""")


def test_unique_per_source_ext_uid(conn, src):
    add(conn)
    with pytest.raises(errors.UniqueViolation):
        add(conn, url="https://x.test/other")


def test_same_ext_uid_allowed_across_sources(conn, src):
    conn.execute("INSERT INTO source (id, kind, base_url) VALUES ('jobs.ac.uk','feed','https://www.jobs.ac.uk')")
    add(conn)
    add(conn, source_id="jobs.ac.uk")


def test_source_fk_enforced(conn):
    with pytest.raises(errors.ForeignKeyViolation):
        add(conn, source_id="nope")


def test_salary_signature_generated(conn, src):
    a = add(conn, ext_uid="a", salary_min=1500, salary_max=1800, salary_currency="EUR", salary_period="month", salary_raw="1500-1800 EUR/month")
    b = add(conn, ext_uid="b", salary_min=1500.00, salary_max=1800, salary_currency="EUR", salary_period="month", salary_raw="1.500 - 1.800 € mesečno")
    c = add(conn, ext_uid="c")
    sig = lambda i: conn.execute("SELECT salary_sig FROM listing WHERE id=%s", (i,)).fetchone()[0]
    assert sig(a) == sig(b) == "1500.00|1800.00|EUR|month"
    assert sig(c) is None


def test_language_must_be_json_array(conn, src):
    add(conn, language='[{"lang":"en","level":"C1","required":true}]')
    with pytest.raises(errors.CheckViolation):
        add(conn, ext_uid="2", language='{"lang":"en"}')


def test_org_master_needs_a_name_and_valid_type(conn):
    conn.execute("INSERT INTO org_master (name_en, country, type, qs_rank_raw, qs_rank) VALUES ('Univ','RS','university','801-850',801)")
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO org_master (country, type) VALUES ('RS','university')")
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO org_master (name_en, country, type) VALUES ('X','RS','ministry')")


def test_org_openalex_id_unique_but_nullable(conn):
    for _ in range(2):
        conn.execute("INSERT INTO org_master (name_en, country, type) VALUES ('A','RS','institute')")
    conn.execute("INSERT INTO org_master (name_en, country, type, openalex_id) VALUES ('B','RS','institute','I123')")
    with pytest.raises(errors.UniqueViolation):
        conn.execute("INSERT INTO org_master (name_en, country, type, openalex_id) VALUES ('C','RS','institute','I123')")


def test_org_delete_keeps_listing(conn, src):
    org = conn.execute("INSERT INTO org_master (name_en, country, type) VALUES ('IPB','RS','institute') RETURNING id").fetchone()[0]
    lid = add(conn, org_id=org, org_raw="Institute of Physics Belgrade")
    conn.execute("DELETE FROM org_master WHERE id=%s", (org,))
    assert conn.execute("SELECT org_id, org_raw FROM listing WHERE id=%s", (lid,)).fetchone() == (None, "Institute of Physics Belgrade")


def test_updated_at_trigger(conn, src):
    lid = add(conn)
    t0 = conn.execute("SELECT updated_at FROM listing WHERE id=%s", (lid,)).fetchone()[0]
    conn.execute("SELECT pg_sleep(0.01)")
    conn.execute("UPDATE listing SET city='Belgrade' WHERE id=%s", (lid,))
    assert conn.execute("SELECT updated_at FROM listing WHERE id=%s", (lid,)).fetchone()[0] > t0
