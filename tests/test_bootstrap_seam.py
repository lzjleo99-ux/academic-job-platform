"""F1's lesson, applied to the bootstrap wiring.

F1 (found 2026-10-10, fixed 2026-10-11 in c34b1c3) was not a broken unit - every FX provider was
individually well tested. It was a broken SEAM: `run_fx_first()` dispatched `ecb.run` and `erapi.run`
through one call shape, but their signatures differ, so ECB died as a KeyError that a bare `except`
turned into a dict nobody ever read. The unit was tested; the line that wired it up had zero coverage.

Census of every wiring/orchestration function in src/ajp found exactly three with zero direct test
coverage, all in bootstrap.py: `seed_all_orgs`, `seed_fx`, `assert_org_count`. The first two are the
only path by which a fresh install gets its orgs and its offline FX fixture, and the third is R3's
own "fail loudly on seed drift" guard. A guard that has never been observed to raise is a promise in
a docstring, not a guarantee - the exact shape of F1.

These tests target the seams, not the units `orgs.seed_orgs`/`orgs.load_seed` (both separately
covered): what `seed_all_orgs` returns versus what actually lands, whether both halves of the seed
really apply, whether `seed_fx`'s ON CONFLICT is idempotent and non-destructive, and above all
whether `assert_org_count` genuinely fails when the counts drift.
"""
import pytest

from ajp.bootstrap import assert_org_count, load_supplement_seed, seed_all_orgs, seed_fx
from ajp.orgs import load_seed

# A currency the fixture seeds, and one it deliberately does not, so "did not clobber" is testable
# rather than assumed.
_SEEDED_CURRENCIES = ("GBP", "USD", "RSD")


def _fx_rows(conn, source=None):
    q = "SELECT currency, per_eur FROM fx_rate"
    params = ()
    if source:
        q += " WHERE source = %s"
        params = (source,)
    return {r[0]: r[1] for r in conn.execute(q, params)}


# --------------------------------------------------------------- seed_all_orgs ----------------

def test_seed_all_orgs_returned_counts_match_what_actually_landed_in_the_db(conn):
    """The seam: `seed_all_orgs` returns `(len(base), len(supplement))` - counts of the INPUT lists, not
    of what `orgs.seed_orgs` actually persisted. If seeding ever silently skipped a row (a duplicate
    slug, a rejected field), the return value would overstate reality while `assert_org_count` - which
    compares len(seed files) against the DB - would then fail for a reason nothing explains. Assert the
    two views agree, so a divergence surfaces here with the real number attached."""
    base_n, supp_n = seed_all_orgs(conn)
    assert base_n == len(load_seed())
    assert supp_n == len(load_supplement_seed())
    landed = conn.execute("SELECT count(*) FROM org_master WHERE country = 'RS'").fetchone()[0]
    assert landed == base_n + supp_n, (
        f"seed_all_orgs reported {base_n}+{supp_n}={base_n + supp_n} but only {landed} RS orgs are in the db"
    )


def test_seed_all_orgs_applies_both_halves_not_just_the_base_seed(conn):
    """The other half of the seam. The OpenAlex reconciliation supplement is loaded from its own file and
    only seeded `if supplement:` - so a missing/empty/mis-parsed supplement file would silently yield a
    short install with no error, exactly the T20 acceptance gap PM caught once already (a fresh install
    ending up with only the base 100). Assert orgs that exist ONLY in the supplement are really there,
    by slug, rather than trusting the count alone."""
    seed_all_orgs(conn)
    supp_only = [o["slug"] for o in load_supplement_seed()[:5]]
    present = {r[0] for r in conn.execute(
        "SELECT slug FROM org_master WHERE slug = ANY(%s)", (supp_only,))}
    assert present == set(supp_only), f"supplement orgs missing after seeding: {set(supp_only) - present}"


# --------------------------------------------------------------- seed_fx ----------------------

def test_seed_fx_is_idempotent_a_second_call_never_duplicates_a_row(conn):
    """The ON CONFLICT seam. `seed_fx` upserts against PRIMARY KEY (rate_date, currency, source); if that
    conflict target were ever wrong the second call would either raise or - worse, with a DO NOTHING -
    silently keep a stale rate. Run it twice and assert exactly one row per currency, with the values the
    fixture states."""
    seed_fx(conn)
    first = dict(_fx_rows(conn, "DEV_FIXTURE"))
    seed_fx(conn)
    second = dict(_fx_rows(conn, "DEV_FIXTURE"))

    assert set(second) == set(_SEEDED_CURRENCIES)
    assert second == first
    n = conn.execute("SELECT count(*) FROM fx_rate WHERE source = 'DEV_FIXTURE'").fetchone()[0]
    assert n == len(_SEEDED_CURRENCIES), f"expected {len(_SEEDED_CURRENCIES)} fixture rows, found {n}"


def test_seed_fx_never_displaces_a_real_provider_row(conn):
    """The precedence seam, and the one that makes F1's fix safe. `fx_source_rank` ranks DEV_FIXTURE last
    (9), so a real ECB/NBS/ERAPI snapshot must always outrank the offline fixture in `fx_rate_latest`.
    The fixture's ON CONFLICT target includes `source`, so it cannot overwrite a real provider's row -
    but that is a property of the SQL, and an unverified property is what F1 was. Seed a real ECB rate,
    run the fixture, and assert the view still resolves to ECB."""
    conn.execute(
        """INSERT INTO fx_rate (rate_date, currency, per_eur, source)
           VALUES (CURRENT_DATE, 'GBP', 0.84763, 'ECB')
           ON CONFLICT (rate_date, currency, source) DO UPDATE SET per_eur = EXCLUDED.per_eur""")
    seed_fx(conn)

    row = conn.execute(
        "SELECT per_eur, source FROM fx_rate_latest WHERE currency = 'GBP'").fetchone()
    assert row[1] == "ECB", f"DEV_FIXTURE displaced a real ECB rate in fx_rate_latest (got {row[1]})"
    assert float(row[0]) == 0.84763
    # ...and the fixture row itself still exists for a currency no real provider covers
    assert _fx_rows(conn, "DEV_FIXTURE")["RSD"] is not None


# --------------------------------------------------------------- assert_org_count -------------

def test_assert_org_count_raises_on_drift_rather_than_silently_passing(conn):
    """THE guard. R3's own acceptance criterion is "assert counts at init and fail loudly on drift", and
    the docstring says "raises, never just logs a warning". That has never been observed: the function
    has zero test coverage, so a regression that turned the raise into a no-op (or `!=` into `<`) would
    ship silently and the next T20-class acceptance gap - a short install nobody notices - would walk
    right back in. This is the single most important test in the file."""
    seed_all_orgs(conn)
    expected = len(load_seed()) + len(load_supplement_seed())
    assert assert_org_count(conn) == expected          # the matching case: no raise, real number back

    with pytest.raises(RuntimeError, match="seed drift"):
        assert_org_count(conn, expected + 1)


def test_assert_org_count_drift_error_names_both_the_expected_and_actual_counts(conn):
    """A drift error that does not say both numbers is not actionable - you cannot tell whether the seed
    file shrank or the DB lost rows. Assert the message carries both, so the failure is diagnosable from
    the log alone."""
    seed_all_orgs(conn)
    actual = conn.execute("SELECT count(*) FROM org_master WHERE country = 'RS'").fetchone()[0]
    with pytest.raises(RuntimeError) as ei:
        assert_org_count(conn, actual - 3)
    msg = str(ei.value)
    assert str(actual - 3) in msg and str(actual) in msg, f"drift message must name both counts: {msg!r}"


def test_assert_org_count_also_catches_a_short_database_not_only_an_overstated_expectation(conn):
    """The guard's comparison must be symmetric. A drift where the DB is SHORT (rows lost) is the
    realistic failure - the T20 gap PM found live was an install with only the base 100 of 186 orgs. If
    the check were one-directional it would miss exactly the case it exists for."""
    seed_all_orgs(conn)
    conn.execute("DELETE FROM org_master WHERE country = 'RS' AND slug LIKE 'inst-%'")
    with pytest.raises(RuntimeError, match="seed drift"):
        assert_org_count(conn)
