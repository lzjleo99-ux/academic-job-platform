from datetime import date
from decimal import Decimal

import httpx
import psycopg
import pytest

from ajp.connectors.base import PoliteClient
from ajp.fx import ecb, erapi, job
from ajp.rawstore import LocalFsStore
from conftest import FIXTURES

ERAPI = (FIXTURES / "erapi-latest-EUR-2026-09-21.json").read_bytes()
DAILY = (FIXTURES / "ecb-daily-2026-09-21.xml").read_bytes()


def _mock(routes):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        body, ctype = routes[req.url.host]
        return httpx.Response(200, content=body, headers={"content-type": ctype})
    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None)


def test_parse_erapi_fixture_has_rsd_and_uses_update_date():
    obs = erapi.parse_erapi(ERAPI)
    rsd = next(o for o in obs if o.currency == "RSD")
    assert rsd.per_eur == Decimal("117.390191") and rsd.rate_date == date(2026, 9, 21)
    assert "EUR" not in {o.currency for o in obs} and len(obs) > 150


@pytest.mark.parametrize("mutate", [
    lambda b: b.replace(b'"result": "success"', b'"result": "error"').replace(b'"result":"success"', b'"result":"error"'),
    lambda b: b.replace(b'"base_code":"EUR"', b'"base_code":"USD"').replace(b'"base_code": "EUR"', b'"base_code": "USD"'),
    lambda b: b'{"result":"success","base_code":"EUR","time_last_update_unix":1,"rates":{"USD":1.1}}',
])
def test_parse_erapi_rejects_bad_payloads(mutate):
    with pytest.raises(ValueError):
        erapi.parse_erapi(mutate(ERAPI))


def test_erapi_run_stores_raw_and_is_idempotent(dsn, tmp_path):
    client = _mock({"open.er-api.com": (ERAPI, "application/json")})
    out = erapi.run(dsn, LocalFsStore(tmp_path), client)
    assert out["has_RSD"] and out["latest_rate_date"] == "2026-09-21"
    assert erapi.run(dsn, LocalFsStore(tmp_path), client)["rows_written_or_changed"] == 0
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT source, per_eur FROM fx_rate WHERE currency='RSD'").fetchone() == ("ERAPI", Decimal("117.39019100"))


def _seed(conn, rows):
    for d, cur, rate, src in rows:
        conn.execute("INSERT INTO fx_rate (rate_date, currency, per_eur, source) VALUES (%s,%s,%s,%s)", (d, cur, rate, src))


def _latest(conn, cur):
    return conn.execute("SELECT source, per_eur FROM fx_rate_latest WHERE currency=%s", (cur,)).fetchone()


def test_precedence_nbs_over_ecb_over_erapi(conn):
    _seed(conn, [("2026-09-21", "USD", 1.15, "ERAPI"), ("2026-09-21", "USD", 1.14, "ECB"),
                 ("2026-09-21", "RSD", 117.4, "ERAPI"), ("2026-09-19", "RSD", 117.3, "NBS")])
    assert _latest(conn, "USD") == ("ECB", Decimal("1.14"))       # ECB beats ERAPI on same date
    assert _latest(conn, "RSD") == ("NBS", Decimal("117.3"))      # NBS wins although 2 days older


def test_weekend_gap_does_not_demote_ecb(conn):
    _seed(conn, [("2026-09-18", "USD", 1.14, "ECB"), ("2026-09-21", "USD", 1.15, "ERAPI")])
    assert _latest(conn, "USD")[0] == "ECB"


def test_stale_higher_precedence_source_loses_to_fresh_one(conn):
    _seed(conn, [("2026-08-01", "BGN", 1.95, "ECB"), ("2026-09-21", "BGN", 1.96, "ERAPI")])
    assert _latest(conn, "BGN")[0] == "ERAPI"


def test_salary_view_records_the_converting_source(dsn, tmp_path):
    ecb.run(dsn, "daily", LocalFsStore(tmp_path), _mock({"www.ecb.europa.eu": (DAILY, "text/xml")}))
    erapi.run(dsn, LocalFsStore(tmp_path), _mock({"open.er-api.com": (ERAPI, "application/json")}))
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("INSERT INTO source (id, kind, base_url) VALUES ('s','manual','https://s.test')")
        for uid, cur in (("1", "RSD"), ("2", "USD"), ("3", "EUR")):
            c.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2, position_kind,
                           salary_min, salary_currency, salary_period, salary_raw)
                         VALUES (%s,'s','u','u','t','RS','phd',117390.191,%s,'year','raw')""", (uid, cur))
        rows = {r[0]: r[1:] for r in c.execute("""SELECT salary_currency, fx_source, salary_min_eur_year
                                                   FROM listing_salary_eur_year""")}
    assert rows["RSD"] == ("ERAPI", Decimal("1000.00"))
    assert rows["USD"][0] == "ECB"
    assert rows["EUR"][1] == Decimal("117390.19") and rows["EUR"][0] is None   # EUR needs no rate, no source


def test_job_runs_providers_independently_and_reports_failure(dsn, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ecb, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ecb down")))
    monkeypatch.setattr(erapi, "run", lambda *a, **k: {"provider": "erapi", "rows_parsed": 1})
    assert job.main(["--dsn", dsn, "--raw-root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "ecb down" in out and '"rows_parsed": 1' in out
