"""GET /v1/meta - taxonomies. Every enum below is copied from the data_contract CHECK constraints
(migrations 0002/0003/0007/0008), never re-typed by hand at drift risk; disciplines_euroscivoc and
language_codes are DISTINCT values actually seen in the data (see degraded_features below) rather than an
authoritative external vocabulary, because none has been loaded yet - D6's spirit (never present an
unverified value as settled fact) applies to reference taxonomies too, not just extracted ad fields.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from psycopg import Connection

from ajp.api import salary as salary_mod
from ajp.api.deps import get_conn
from ajp.api.filters import DEGREE_VALUES, DURATION_BUCKETS
from ajp.api.schemas import MetaResponse

router = APIRouter(prefix="/v1", tags=["meta"])

RS_RESEARCH_TITLE = ("istrazivac_pripravnik", "istrazivac_saradnik", "naucni_saradnik",
                     "visi_naucni_saradnik", "naucni_savetnik")
POSITION_KIND = ("phd", "postdoc", "research_assistant", "researcher_title", "professor", "lecturer",
                 "technician", "admin", "intern", "other")
KIND = ("employment", "enrollment", "grant_call", "internship", "other")
FT_PT = ("full", "part", "both", "unknown")

DEGRADED_FEATURES = [
    {"feature": "discipline_hierarchy",
     "reason": "no authoritative EuroSciVoc concept scheme has been loaded (no live/verifiable source was "
                "fetched for T14); disciplines_euroscivoc below lists only codes actually present in ingested "
                "rows, flat, with no parent/child relationships - filter/facet is overlap-only, never expands "
                "to descendants"},
    {"feature": "geonames_city_typeahead",
     "reason": "packet 007 permits deferring this to M3: city filter uses a static list seeded from ingested "
                "rows' own city values instead of a GeoNames-backed typeahead"},
]


@router.get("/meta", response_model=MetaResponse)
def meta(conn: Connection = Depends(get_conn)) -> MetaResponse:
    disciplines = conn.execute(
        "SELECT d AS code, count(*) AS n FROM listing CROSS JOIN unnest(disciplines_euroscivoc) AS d "
        "GROUP BY d ORDER BY n DESC"
    ).fetchall()
    languages = conn.execute(
        "SELECT DISTINCT le->>'lang' AS lang FROM listing CROSS JOIN LATERAL jsonb_array_elements(language) AS le "
        "WHERE le->>'lang' IS NOT NULL ORDER BY 1"
    ).fetchall()
    currencies = conn.execute("SELECT DISTINCT currency FROM fx_rate_latest ORDER BY 1").fetchall()
    fx_as_of = conn.execute("SELECT max(rate_date) AS d FROM fx_rate_latest").fetchone()["d"]
    return MetaResponse(
        disciplines_euroscivoc=[dict(r) for r in disciplines], disciplines_taxonomy_complete=False,
        rs_research_title=list(RS_RESEARCH_TITLE), position_kind=list(POSITION_KIND), kind=list(KIND),
        degree_required=list(DEGREE_VALUES), ft_pt=list(FT_PT),
        currencies=["EUR"] + [r["currency"] for r in currencies], fx_as_of=fx_as_of,
        language_codes=[r["lang"] for r in languages],
        salary_buckets=[{"name": n, "min_eur_year": lo, "max_eur_year": hi} for n, lo, hi in salary_mod.SALARY_BUCKETS],
        duration_buckets=list(DURATION_BUCKETS), degraded_features=DEGRADED_FEATURES,
    )
