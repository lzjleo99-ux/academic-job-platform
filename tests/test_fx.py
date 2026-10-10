from decimal import Decimal

import httpx
import psycopg
import pytest

from ajp.connectors.base import FetchFailed, PoliteClient
from ajp.fx import ecb
from ajp.rawstore import LocalFsStore, get_raw
from ajp.robots import RobotsDenied
from conftest import FIXTURES

DAILY = (FIXTURES / "ecb-daily-2026-09-21.xml").read_bytes()

HIST = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<gesmes:subject>Reference rates</gesmes:subject><Cube>
<Cube time="2026-09-21"><Cube currency="USD" rate="1.10"/><Cube currency="CYP" rate="N/A"/></Cube>
<Cube time="2026-09-18"><Cube currency="USD" rate="1.11"/></Cube></Cube></gesmes:Envelope>"""


def test_parse_daily_fixture():
    obs = ecb.parse_ecb_xml(DAILY)
    assert {o.rate_date.isoformat() for o in obs} == {"2026-09-21"}
    assert len(obs) == 29
    assert "RSD" not in {o.currency for o in obs}
    usd = next(o for o in obs if o.currency == "USD")
    assert isinstance(usd.per_eur, Decimal) and usd.per_eur > 0
    ecb.validate(obs, "daily")


def test_parse_history_skips_na_and_keeps_all_dates():
    obs = ecb.parse_ecb_xml(HIST)
    assert [(o.rate_date.isoformat(), o.currency, str(o.per_eur)) for o in obs] == [
        ("2026-09-21", "USD", "1.10"), ("2026-09-18", "USD", "1.11")]


def test_parse_rejects_garbage_and_truncated_daily():
    with pytest.raises(ValueError):
        ecb.parse_ecb_xml(b"<a xmlns='http://www.ecb.int/vocabulary/2002-08-01/eurofxref'/>")
    with pytest.raises(ValueError, match="expected >="):
        ecb.validate(ecb.parse_ecb_xml(HIST), "daily")


def test_xml_entity_bombs_are_refused():
    from defusedxml.common import DefusedXmlException
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><x>&b;</x>'
    with pytest.raises(DefusedXmlException):
        ecb.parse_ecb_xml(bomb)


def _client(body=DAILY, status=200, robots="User-agent: *\nDisallow: /private/\n", robots_status=200):
    seen = []
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=robots)
        seen.append(req)
        return httpx.Response(status, content=body, headers={"content-type": "text/xml"})
    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), rps=1000, sleep=lambda s: None), seen


def test_run_writes_rates_snapshot_and_is_idempotent(dsn, tmp_path):
    store = LocalFsStore(tmp_path)
    client, seen = _client()
    out = ecb.run(dsn, "daily", store, client)
    assert out["rows_parsed"] == 29 and out["rows_written_or_changed"] == 29
    assert out["latest_rate_date"] == "2026-09-21"
    assert seen[0].url == "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
    assert "ajp-research-bot/" in seen[0].headers["user-agent"] and "lzjleo99" not in seen[0].headers["user-agent"]

    content, meta = get_raw(store, out["raw_key"])          # raw snapshot retained
    assert content == DAILY and meta["extra"] == {"feed": "daily"}

    again = ecb.run(dsn, "daily", store, client)
    assert again["rows_written_or_changed"] == 0
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT count(*), min(source), max(source) FROM fx_rate").fetchone() == (29, "ECB", "ECB")
        assert c.execute("SELECT raw_ref FROM fx_rate LIMIT 1").fetchone()[0] == out["raw_key"]


def test_run_updates_a_corrected_rate(dsn, tmp_path):
    ecb.run(dsn, "daily", LocalFsStore(tmp_path), _client()[0])
    fixed = DAILY.replace(b'currency=\'USD\' rate=\'', b'currency=\'USD\' rate=\'9')
    out = ecb.run(dsn, "daily", LocalFsStore(tmp_path), _client(fixed)[0])
    assert out["rows_written_or_changed"] == 1


def test_run_fails_loudly_and_writes_nothing_on_http_error(dsn, tmp_path):
    with pytest.raises(FetchFailed):
        ecb.run(dsn, "daily", LocalFsStore(tmp_path), _client(b"nope", 503)[0])
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT count(*) FROM fx_rate").fetchone()[0] == 0


def test_salary_eur_view(dsn, tmp_path):
    ecb.run(dsn, "daily", LocalFsStore(tmp_path), _client()[0])
    with psycopg.connect(dsn, autocommit=True) as c:
        usd = c.execute("SELECT per_eur FROM fx_rate WHERE currency='USD'").fetchone()[0]
        c.execute("INSERT INTO source (id, kind, base_url) VALUES ('s','manual','https://s.test')")
        def add(uid, cur, period, lo, hi=None):
            return c.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2, position_kind,
                                  salary_min, salary_max, salary_currency, salary_period, salary_raw)
                                VALUES (%s,'s','u','u','t','RS','phd',%s,%s,%s,%s,'raw') RETURNING id""",
                             (uid, lo, hi, cur, period)).fetchone()[0]
        eur_m, usd_y, rsd_m, eur_h, eur_s = (add("1", "EUR", "month", 2000, 2500), add("2", "USD", "year", 60000),
                                            add("3", "RSD", "month", 150000), add("4", "EUR", "hour", 30),
                                            add("5", "EUR", "stipend", 1200))
        add_none = c.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2, position_kind)
                                VALUES ('6','s','u','u','t','RS','phd') RETURNING id""").fetchone()[0]
        v = {r[0]: r[1:] for r in c.execute("""SELECT listing_id, salary_min_eur_year, salary_max_eur_year, fx_rate_date
                                               FROM listing_salary_eur_year""")}
        assert v[eur_m][:2] == (Decimal("24000.00"), Decimal("30000.00"))
        assert v[usd_y][0] == (Decimal(60000) / usd).quantize(Decimal("0.01")) and v[usd_y][1] is None
        assert v[rsd_m][:2] == (None, None)        # no RSD rate in ECB -> stays NULL, never guessed
        assert v[eur_h][:2] == (None, None)        # hourly: not annualised
        assert v[eur_s][:2] == (None, None)        # stipend of unstated period: not annualised
        assert add_none not in v                   # no stated salary -> no row


@pytest.mark.live
def test_live_ecb_daily_feed(dsn, tmp_path):
    out = ecb.run(dsn, "daily", LocalFsStore(tmp_path))
    assert out["rows_parsed"] >= 20 and "USD" in out["currencies_on_latest"]


def test_fx_fetch_refuses_when_robots_denies_or_is_unavailable(dsn, tmp_path):
    with pytest.raises(RobotsDenied):
        ecb.run(dsn, "daily", LocalFsStore(tmp_path), _client(robots="User-agent: *\nDisallow: /stats/\n")[0])
    with pytest.raises(RobotsDenied):   # robots.txt 5xx -> RFC 9309: assume disallowed
        ecb.run(dsn, "daily", LocalFsStore(tmp_path), _client(robots_status=503)[0])
    client, seen = _client(robots_status=404)   # robots.txt 404 -> no restrictions
    ecb.run(dsn, "daily", LocalFsStore(tmp_path), client)
    assert len(seen) == 1
