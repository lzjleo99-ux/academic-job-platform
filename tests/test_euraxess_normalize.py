"""Normalizer tests use SYNTHETIC fixtures only (authored for this repo; never captured pages)."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ajp.euraxess_normalize import (NormalizeError, coverage, derive_status, normalize_detail,
                                    parse_deadline, parse_definition_list)
from ajp.extract.salary import extract_salary

FX = Path(__file__).parent / "fixtures" / "synthetic"
NOW = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)
MSCA = (FX / "euraxess_detail_synthetic_msca.html").read_text()
STALE = (FX / "euraxess_detail_synthetic_stale.html").read_text()


def norm(html, **kw):
    kw.setdefault("now", NOW)
    return normalize_detail(html, ext_uid="SYN-1", url="https://example.invalid/1", raw_html_ref="k", **kw)


def test_fixtures_are_marked_synthetic():
    for f in FX.glob("*.html"):
        assert "SYNTHETIC FIXTURE" in f.read_text()


def test_definition_list_keeps_document_order_and_repeats():
    f = parse_definition_list("<dl><dt>A</dt><dd>1</dd><dt>B:</dt><dd>2</dd><dd>3</dd><dt>A</dt><dd>4</dd></dl>")
    assert f == {"a": ["1", "4"], "b": ["2", "3"]}       # regression: css('dt, dd') groups dt before dd


def test_definition_list_reads_li_values_separately():
    assert parse_definition_list("<dl><dt>Languages</dt><dd><ul><li>English</li><li>Serbian</li></ul></dd></dl>") == \
        {"languages": ["English", "Serbian"]}


def test_full_ad_maps_to_contract_fields():
    n = norm(MSCA); r = n.row
    assert (r["country_iso2"], r["city"], r["org_raw"], r["position_kind"], r["contract"], r["ft_pt"]) == \
        ("RS", "Belgrade", "Example Institute of Physics", "phd", "fixed", "full")
    assert r["disciplines_euroscivoc"] == ["Physics » Quantum physics", "Engineering » Materials engineering"]
    assert r["degree_required"] == "msc" and r["ad_lang"] == "en"
    assert r["language"] == [{"lang": "en", "level": "Excellent", "required": None}]
    assert r["contact_emails"] == ["hr@example.invalid", "recruit@example.invalid"] and r["contact_scope"] == "ad"
    assert r["foreigner_eligible"] == "yes"                       # ad text says "any nationality"
    assert n.extras["hours_per_week"] == "40" and n.extras["researcher_profile"].startswith("First Stage")


def test_d6_nothing_invented_when_not_stated():
    r = norm(STALE).row
    for k in ("salary_min", "salary_raw", "duration_months_v", "degree_required", "study_while_work_ok_v", "hours_pct", "city"):
        assert k not in r                                          # absent, not defaulted
    assert r["foreigner_eligible"] == "unknown" and r["language"] == [] and r["disciplines_euroscivoc"] == []
    assert r["position_kind"] == "postdoc"                         # "Postdoctoral" must not read as phd


def test_salary_extracted_from_body_text_with_conf_and_raw_span():
    r = norm(MSCA).row
    assert (r["salary_min"], r["salary_max"], r["salary_currency"], r["salary_period"], r["salary_gross_net"]) == \
        (Decimal("43000"), Decimal("43000"), "EUR", "year", "gross")
    assert "43000 EUR" in r["salary_raw"] and 0.5 <= r["salary_conf"] <= 0.95


@pytest.mark.parametrize("text,expect", [
    ("Net 1.500 - 1.800 € monthly", (1500, 1800, "EUR", "month", "net")),
    ("from 2000 EUR monthly", (2000, None, "EUR", "month", None)),
    ("salary up to 3.000 € per month", (None, 3000, "EUR", "month", None)),
    ("Stipend of 2k EUR", (2000, 2000, "EUR", None, None)),
    ("Plata: 150.000 RSD neto mesečno", (150000, 150000, "RSD", "month", "net")),
])
def test_salary_variants(text, expect):
    h = extract_salary(text)
    d = lambda x: None if x is None else Decimal(x)
    assert (h.min, h.max, h.currency, h.period, h.gross_net) == (d(expect[0]), d(expect[1]), *expect[2:])


@pytest.mark.parametrize("text", ["No money mentioned, call 2025 now", "Grant agreement 101073", "budget 5 people", "1 EUR"])
def test_no_salary_when_no_explicit_currency_amount(text):
    assert extract_salary(text) is None


def test_deadline_timezone_and_status_logic_stale_vs_open():
    stale = norm(STALE).row
    assert stale["deadline"] == datetime(2025, 5, 31, 21, 59, tzinfo=timezone.utc)   # 23:59 CEST
    assert stale["status"] == "expired"                                             # presence in a listing != open
    assert norm(STALE, now=datetime(2025, 5, 1, tzinfo=timezone.utc)).row["status"] == "open"
    assert norm(MSCA).row["status"] == "open"
    d = stale["deadline"]
    assert derive_status(d, d) == "expired" and derive_status(d, d.replace(minute=58)) == "open"
    assert derive_status(None, NOW) == "open"


def test_deadline_assumptions_are_flagged_not_hidden():
    p = parse_deadline("31 May 2027")
    assert p["tz_assumed"] and p["time_assumed"] and p["deadline"] == datetime(2027, 5, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert parse_deadline("31 May 2027 - 10:00 (Nowhere/Land)")["tz_assumed"]
    assert parse_deadline("sometime soon") is None
    r = norm(MSCA.replace("31 May 2027 - 23:59 (Europe/Belgrade)", "31 May 2027"))
    assert r.extras["deadline_assumptions"] == {"tz_assumed_utc": True, "end_of_day_assumed": True}


def test_conflicting_education_levels_become_unknown():
    html = MSCA.replace("Master Degree or equivalent</dd>\n  <dt class=\"ecl-description-list__term\">Researcher", "PhD or equivalent</dd>\n  <dt class=\"ecl-description-list__term\">Researcher")
    assert norm(html).row["degree_required"] == "unknown"


@pytest.mark.parametrize("bad", [STALE.replace("<h1>Postdoctoral Researcher in Example Structural Analysis</h1>", ""),
                                 STALE.replace("<dd>Serbia</dd>", "<dd>Atlantis</dd>"),
                                 STALE.replace("<dt>Country</dt><dd>Serbia</dd>", "")])
def test_missing_title_or_country_is_an_error_not_a_guess(bad):
    with pytest.raises(NormalizeError):
        norm(bad)


def test_coverage_stats():
    c = coverage([norm(MSCA).row, norm(STALE).row])
    assert c["rows"] == 2 and c["salary_min"] == 1 and c["deadline"] == 2 and c["degree_required"] == 1
