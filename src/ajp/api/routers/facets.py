"""GET /v1/facets - one count per bucket for each of the 12 dimensions packet 007 names, computed with the
standard multi-select-facet rule: a dimension's own counts are computed as if every OTHER active filter
still applies but that dimension's own filter does not (so picking a value in a facet never makes its own
counts collapse to just the already-picked value).
"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends
from psycopg import Connection

from ajp.api import salary as salary_mod
from ajp.api.deps import get_conn
from ajp.api.filters import ListingFilters, duration_bucket_case_sql, where_sql
from ajp.api.params import listing_filters
from ajp.api.rows import BASE_FROM
from ajp.api.schemas import FacetBucket, FacetsResponse

router = APIRouter(prefix="/v1", tags=["facets"])

_SIMPLE_COLUMNS = {
    "country": "l.country_iso2", "city": "l.city", "kind": "l.kind", "position_kind": "l.position_kind",
    "ft_pt": "l.ft_pt", "degree_required": "COALESCE(l.degree_required, 'unknown')",
    "foreigner": "l.foreigner_eligible", "location_class": "l.location_class",
}
# facet name -> the filters.py DIMENSIONS key it must exclude from its own baseline
_EXCLUDE = {
    "country": "country", "city": "city", "kind": "kind", "position_kind": "position_kind", "ft_pt": "ft_pt",
    "degree_required": "degree_required", "foreigner": "foreigner", "discipline": "discipline",
    "language": "language", "source": "source", "duration_bucket": "duration", "salary_bucket": "salary",
    "location_class": "location_class",
}


def _run(conn: Connection, sql: str, params: Dict) -> List[FacetBucket]:
    rows = conn.execute(sql, params).fetchall()
    return [FacetBucket(value=r["value"], count=r["n"]) for r in rows if r["value"] is not None]


def _simple(conn: Connection, f: ListingFilters, name: str, column: str) -> List[FacetBucket]:
    where, params = where_sql(f, exclude=_EXCLUDE[name])
    sql = f"SELECT {column} AS value, count(*) AS n {BASE_FROM} WHERE {where} GROUP BY {column} ORDER BY n DESC"
    return _run(conn, sql, params)


def _discipline(conn: Connection, f: ListingFilters) -> List[FacetBucket]:
    where, params = where_sql(f, exclude="discipline")
    sql = (f"SELECT d AS value, count(*) AS n {BASE_FROM} CROSS JOIN unnest(l.disciplines_euroscivoc) AS d "
           f"WHERE {where} GROUP BY d ORDER BY n DESC")
    return _run(conn, sql, params)


def _language(conn: Connection, f: ListingFilters) -> List[FacetBucket]:
    where, params = where_sql(f, exclude="language")
    sql = (f"SELECT le->>'lang' AS value, count(*) AS n {BASE_FROM} "
           f"CROSS JOIN LATERAL jsonb_array_elements(l.language) AS le WHERE {where} GROUP BY 1 ORDER BY n DESC")
    return _run(conn, sql, params)


def _source(conn: Connection, f: ListingFilters) -> List[FacetBucket]:
    where, params = where_sql(f, exclude="source")
    sql = (f"SELECT se->>'source' AS value, count(*) AS n {BASE_FROM} "
           f"CROSS JOIN LATERAL jsonb_array_elements(l.sources) AS se WHERE {where} GROUP BY 1 ORDER BY n DESC")
    return _run(conn, sql, params)


def _duration_bucket(conn: Connection, f: ListingFilters) -> List[FacetBucket]:
    where, params = where_sql(f, exclude="duration")
    case_sql = duration_bucket_case_sql()
    sql = f"SELECT {case_sql} AS value, count(*) AS n {BASE_FROM} WHERE {where} GROUP BY 1 ORDER BY n DESC"
    return _run(conn, sql, params)


def _salary_bucket(conn: Connection, f: ListingFilters) -> List[FacetBucket]:
    where, params = where_sql(f, exclude="salary")
    lo_expr = f"COALESCE({salary_mod.eur_year_sql('min')}, {salary_mod.eur_year_sql('max')})"
    hi_expr = f"COALESCE({salary_mod.eur_year_sql('max')}, {salary_mod.eur_year_sql('min')})"
    is_null = f"({salary_mod.eur_year_sql('min')} IS NULL AND {salary_mod.eur_year_sql('max')} IS NULL)"
    case_sql = salary_mod.bucket_case_sql(lo_expr, hi_expr, is_null)
    sql = f"SELECT {case_sql} AS value, count(*) AS n {BASE_FROM} WHERE {where} GROUP BY 1 ORDER BY n DESC"
    return _run(conn, sql, params)


@router.get("/facets", response_model=FacetsResponse)
def facets(f: ListingFilters = Depends(listing_filters), conn: Connection = Depends(get_conn)) -> FacetsResponse:
    return FacetsResponse(
        **{name: _simple(conn, f, name, col) for name, col in _SIMPLE_COLUMNS.items()},
        discipline=_discipline(conn, f), language=_language(conn, f), source=_source(conn, f),
        duration_bucket=_duration_bucket(conn, f), salary_bucket=_salary_bucket(conn, f),
    )
