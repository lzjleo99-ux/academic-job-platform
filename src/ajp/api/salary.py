"""T14 filter_semantics.salary: normalize salary_min/max to EUR/year, in the API layer only (the DB's own
``listing_salary_eur_year`` view, migration 0004, deliberately stops at year/month - it predates this task
and other code may still depend on its narrower behaviour, so it is left alone). Conversion rules, exactly
as packet 007 states them:

    period 'year'      x1
    period 'month'     x12
    period 'hour'      x40 (hours/week) x52 (weeks/year)          <- a stated assumption, not an extracted fact
    period 'stipend'   annualised via duration_months.v (amount / duration_months * 12); NULL if no duration
    currency != EUR    divided by fx_rate_latest.per_eur for that currency; NULL if no rate on file

Every conversion is logged: the response carries ``salary_eur_year_basis`` per listing (fx_rate_date,
fx_source, assumption applied) so a client can see exactly how a figure was derived - never a bare number
presented as if the ad itself stated it in EUR/year (D6).
"""
from __future__ import annotations

from typing import Optional, Tuple

# Provisional UI buckets (not extracted data - a display/filter grouping the API defines itself).
# boundaries in EUR/year; upper bound exclusive except the last, open-ended bucket. 'unspecified' (a
# null-salary listing) is a separate sentinel handled by filters.py/routers.facets, not a row here.
SALARY_BUCKETS = (
    ("lt_10k", None, 10_000),
    ("10_25k", 10_000, 25_000),
    ("25_40k", 25_000, 40_000),
    ("40_60k", 40_000, 60_000),
    ("60_100k", 60_000, 100_000),
    ("gte_100k", 100_000, None),
)

# One shared SQL expression (per-listing amount -> EUR/year), parameter-free so it is safe to splice into
# several queries (listings/facets/detail) via a CTE. `l` is the listing alias, `fx` a LEFT JOIN of
# fx_rate_latest on fx.currency = l.salary_currency (see filters.SALARY_CTE for the full CTE).
_ANNUALIZED = """
    CASE l.salary_period
        WHEN 'year' THEN l.salary_{bound}
        WHEN 'month' THEN l.salary_{bound} * 12
        WHEN 'hour' THEN l.salary_{bound} * 40 * 52
        WHEN 'stipend' THEN CASE WHEN l.duration_months_v IS NOT NULL
                                  THEN l.salary_{bound} / l.duration_months_v * 12 END
        ELSE NULL
    END
"""


def annualized_sql(bound: str) -> str:
    """``bound`` is 'min' or 'max'. Returns the per-currency (not-yet-EUR) annualized amount expression."""
    assert bound in ("min", "max")
    return _ANNUALIZED.format(bound=bound)


# Full per-listing EUR/year expression: annualize, then divide by the fx rate (1 for EUR itself; NULL, and
# so the whole expression NULL, when the currency isn't EUR and no rate is on file - never guessed to 1).
def eur_year_sql(bound: str) -> str:
    per_eur = "CASE WHEN l.salary_currency = 'EUR' THEN 1 ELSE fx.per_eur END"
    return f"(({annualized_sql(bound)}) / NULLIF({per_eur}, 0))"


def bucket_for(amount_eur_year: Optional[float]) -> Optional[str]:
    if amount_eur_year is None:
        return None
    for name, lo, hi in SALARY_BUCKETS:
        if (lo is None or amount_eur_year >= lo) and (hi is None or amount_eur_year < hi):
            return name
    return None


def bucket_bounds(name: str) -> Optional[Tuple[Optional[float], Optional[float]]]:
    for n, lo, hi in SALARY_BUCKETS:
        if n == name:
            return lo, hi
    return None


def bucket_case_sql(lo_expr: str, hi_expr: str, is_null_expr: str) -> str:
    """/v1/facets' salary_bucket dimension, as one CASE expression over the same lo/hi EUR/year expressions
    the salary filter uses (see filters._salary) - 'unspecified' for a listing with no computable amount at
    all (no stated salary, or a currency/period the API cannot convert), never silently dropped from the facet."""
    whens = []
    for name, lo, hi in SALARY_BUCKETS:
        cond = []
        if lo is not None:
            cond.append(f"{hi_expr} >= {lo}")
        if hi is not None:
            cond.append(f"{lo_expr} < {hi}")
        whens.append(f"WHEN {' AND '.join(cond)} THEN '{name}'")
    return f"(CASE WHEN {is_null_expr} THEN 'unspecified' " + " ".join(whens) + " END)"
