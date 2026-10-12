"""The shared FX writer's seams - F1's lesson applied to the other half of the pipeline.

F1 was about providers never being CALLED (a dispatch/signature seam). `ajp.fx.common.upsert_rates` is
the opposite and equally load-bearing: it is the SINGLE persistence path both ECB and ERAPI go through,
so a regression here breaks every FX rate at once while each provider's own parse test stays green. It
had zero direct test coverage.

The interesting seam is the return value. `_UPSERT` ends with
`ON CONFLICT ... DO UPDATE ... WHERE fx_rate.per_eur IS DISTINCT FROM EXCLUDED.per_eur`, and the
function returns `cur.rowcount` from an `executemany`. So the documented contract - "rows inserted or
changed" - depends on two separate behaviours that must not drift apart: psycopg's rowcount semantics
across executemany, and Postgres counting a conflict-updated-but-unchanged row as zero affected rows.
Nothing asserts either one. These tests pin the contract from the outside.
"""
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from ajp.fx.common import FxObs, upsert_rates

D = date(2026, 10, 9)


def _obs(currency, per_eur, day=D):
    return FxObs(rate_date=day, currency=currency, per_eur=Decimal(str(per_eur)))


def _rows(conn, source=None):
    q = "SELECT currency, per_eur, raw_ref FROM fx_rate"
    params = ()
    if source:
        q += " WHERE source = %s"
        params = (source,)
    return {r[0]: (r[1], r[2]) for r in conn.execute(q, params)}


def test_upsert_rates_returns_only_the_rows_it_actually_changed(dsn):
    """The contract: "rows inserted or changed", NOT the number of observations handed in. Pass 5
    observations of which 3 are already stored unchanged - a re-crawl re-finding the same three rates
    plus two new ones. If the return ever became len(obs) (a plausible refactor: counting the input
    instead of the effect) every caller's "rows_new/rows_updated" ledger figure would silently
    overstate reality, which is exactly the ledger-honesty discipline run_ledger exists to uphold."""
    upsert_rates(dsn, [_obs("USD", 1.10), _obs("GBP", 0.85), _obs("CHF", 0.93)], "ECB", "ref/a")

    written = upsert_rates(dsn, [_obs("USD", 1.10), _obs("GBP", 0.85), _obs("CHF", 0.93),
                                 _obs("SEK", 10.9), _obs("NOK", 11.7)], "ECB", "ref/b")

    assert written == 2, f"expected 2 changed rows out of 5 observations, got {written}"
    with psycopg.connect(dsn) as c:
        assert len(_rows(c, "ECB")) == 5


def test_upsert_rates_is_idempotent_a_repeat_writes_nothing(dsn):
    """The `WHERE ... IS DISTINCT FROM` clause is what makes a 24h re-crawl cheap and the ledger honest.
    Drop it (or lose it in a refactor) and every cycle would report all ~29 currencies as "changed"
    forever, making the ledger useless for spotting a source that actually moved."""
    first = upsert_rates(dsn, [_obs("USD", 1.10), _obs("GBP", 0.85)], "ECB", "ref/a")
    assert first == 2
    assert upsert_rates(dsn, [_obs("USD", 1.10), _obs("GBP", 0.85)], "ECB", "ref/a") == 0


def test_upsert_rates_labels_every_row_with_the_source_it_was_given(dsn):
    """The precedence seam. `fx_source_rank` orders NBS(1) > ECB(2) > ERAPI(3) > everything else(9), and
    `fx_rate_latest` resolves one rate per currency from that ordering. If `upsert_rates` ever wrote a
    row under the wrong source label, a currency would silently resolve to the wrong precedence - and
    F1's whole point was that this ordering had been quietly inverted in practice for months. Assert
    the label is what the caller passed, per provider."""
    upsert_rates(dsn, [_obs("GBP", 0.84763)], "ECB", "ref/ecb")
    upsert_rates(dsn, [_obs("GBP", 0.85100)], "ERAPI", "ref/erapi")

    with psycopg.connect(dsn) as c:
        assert _rows(c, "ECB")["GBP"][0] == Decimal("0.84763")
        assert _rows(c, "ERAPI")["GBP"][0] == Decimal("0.85100")
        winner = c.execute(
            "SELECT source FROM fx_rate_latest WHERE currency = 'GBP'").fetchone()[0]
        assert winner == "ECB", f"ECB must outrank ERAPI at equal rate_date, got {winner}"


def test_upsert_rates_records_the_raw_ref_so_every_rate_is_traceable(dsn):
    """D6's evidence chain, at the FX layer: `raw_ref` is the object-store key of the snapshot the rate
    came from. A rate with no raw_ref cannot be traced back to the bytes that produced it, which is the
    same anti-fabrication guarantee the listing table enforces with CHECK constraints."""
    upsert_rates(dsn, [_obs("USD", 1.10)], "ECB", "raw_html/v1/ecb/2026/10/09/deadbeef.xml.gz")
    with psycopg.connect(dsn) as c:
        assert _rows(c, "ECB")["USD"][1] == "raw_html/v1/ecb/2026/10/09/deadbeef.xml.gz"


def test_upsert_rates_corrects_a_wrong_rate_in_place_rather_than_duplicating_it(dsn):
    """The DO UPDATE path. A corrected re-publication must overwrite the row for that
    (rate_date, currency, source), not add a second one - otherwise `fx_rate_latest`'s DISTINCT ON
    would pick arbitrarily between two conflicting rates for the same date and source."""
    upsert_rates(dsn, [_obs("USD", 9.99)], "ECB", "ref/first")
    upsert_rates(dsn, [_obs("USD", 1.10)], "ECB", "ref/second")

    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "SELECT per_eur, raw_ref FROM fx_rate WHERE currency='USD' AND source='ECB'").fetchall()
        assert len(rows) == 1, f"correction duplicated the row: {rows}"
        assert rows[0][0] == Decimal("1.10") and rows[0][1] == "ref/second"


def test_upsert_rates_with_no_observations_writes_nothing_and_returns_zero(dsn):
    """The empty edge case. A provider whose feed parsed to zero currencies must report 0, not crash on
    an empty executemany and not report a phantom row count - a feed that silently went empty is
    exactly the kind of degradation that must stay visible."""
    assert upsert_rates(dsn, [], "ECB", "ref/empty") == 0
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT count(*) FROM fx_rate").fetchone()[0] == 0


@pytest.mark.parametrize("bad", [
    pytest.param({"currency": "usd"}, id="lowercase-currency"),
    pytest.param({"currency": "USDD"}, id="four-letter-currency"),
    pytest.param({"per_eur": Decimal("-1")}, id="non-positive-rate"),
])
def test_upsert_rates_lets_the_schema_reject_malformed_observations(dsn, bad):
    """D6 at the writer boundary: a malformed observation must be refused by the schema's own CHECK
    constraints, never coerced into a plausible-looking row. If these constraints were ever relaxed,
    garbage would enter the FX table and silently corrupt every salary conversion downstream."""
    kw = {"currency": "USD", "per_eur": Decimal("1.10")}
    kw.update(bad)
    with pytest.raises(psycopg.errors.CheckViolation):
        upsert_rates(dsn, [_obs(**kw)], "ECB", "ref/bad")
