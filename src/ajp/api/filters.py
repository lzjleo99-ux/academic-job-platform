"""T14 filter_semantics: the 13 named filters (country, city, discipline, foreigner, salary, duration,
ft_pt, study_while_working, kind, position_kind, language, degree_required, deadline) plus the two extra
chips filter_semantics also names (source, text). One ``ListingFilters`` is built by a router from its own
query params and consumed by /v1/listings, /v1/listings/{id}'s sibling queries and /v1/facets alike, so a
facet's counts and a search's rows are always computed by the exact same predicate.

Every clause function returns ``None`` when its filter is inactive (never an always-true fragment - keeps
the base-query fast path clean) or ``(sql_fragment, params)`` otherwise. ``where_sql`` joins the active
ones with AND; a facet asks for the SAME set with its own dimension's clause excluded (``exclude=...``),
which is the standard multi-select-facet trick: "counts as if every OTHER filter is already applied".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from ajp.api import salary

Clause = Optional[Tuple[str, Dict[str, Any]]]

DURATION_BUCKETS = ("lt6", "6_12", "12_24", "24_36", "gt36", "permanent", "unknown")
DEGREE_VALUES = ("bsc", "msc", "phd", "none", "unknown")
SWOW_VALUES = ("true", "false", "unknown")


@dataclass
class ListingFilters:
    country: Optional[List[str]] = None
    city: Optional[List[str]] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    r_km: Optional[float] = None
    discipline: Optional[List[str]] = None
    foreigner: Optional[List[str]] = None                       # yes/no/unknown
    salary_min: Optional[float] = None                          # EUR/year
    salary_max: Optional[float] = None                          # EUR/year
    salary_bucket: Optional[List[str]] = None
    salary_include_unspecified: bool = True
    duration_bucket: Optional[List[str]] = None
    ft_pt: Optional[List[str]] = None
    study_while_working: Optional[List[str]] = None             # true/false/unknown
    kind: Optional[List[str]] = None
    position_kind: Optional[List[str]] = None
    language: Optional[List[str]] = None
    language_level: Optional[str] = None
    degree_required: Optional[List[str]] = None
    deadline_bucket: Optional[str] = None                       # closing_soon | this_month
    deadline_from: Optional[date] = None
    deadline_to: Optional[date] = None
    status: Optional[List[str]] = None
    source: Optional[List[str]] = None
    text: Optional[str] = None
    location_class: Optional[List[str]] = None                  # located|remote|hybrid|unknown (T23, PM ruling R2)


# ---- one clause function per dimension, keyed by the name a facet/exclude= refers to it by -----------------
def _country(f: ListingFilters) -> Clause:
    return ("l.country_iso2 = ANY(%(country)s)", {"country": f.country}) if f.country else None


def _city(f: ListingFilters) -> Clause:
    parts, params = [], {}
    if f.city:
        parts.append("l.city = ANY(%(city)s)")
        params["city"] = f.city
    if f.lat is not None and f.lon is not None and f.r_km is not None:
        parts.append(
            "(l.lat IS NOT NULL AND l.lon IS NOT NULL AND 6371 * acos(LEAST(1, GREATEST(-1, "
            "cos(radians(%(c_lat)s)) * cos(radians(l.lat)) * cos(radians(l.lon) - radians(%(c_lon)s)) "
            "+ sin(radians(%(c_lat)s)) * sin(radians(l.lat))))) <= %(c_rkm)s)"
        )
        params.update(c_lat=f.lat, c_lon=f.lon, c_rkm=f.r_km)
    return ("(" + " OR ".join(parts) + ")", params) if parts else None


def _discipline(f: ListingFilters) -> Clause:
    # Flat overlap only - "include descendants" needs a real EuroSciVoc hierarchy, not yet loaded
    # (see /v1/meta's degraded-feature note); documented, never silently claimed as done.
    return ("l.disciplines_euroscivoc && %(discipline)s", {"discipline": f.discipline}) if f.discipline else None


def _foreigner(f: ListingFilters) -> Clause:
    return ("l.foreigner_eligible = ANY(%(foreigner)s)", {"foreigner": f.foreigner}) if f.foreigner else None


def _salary(f: ListingFilters) -> Clause:
    """Two independent modes, never silently merged:
      - discrete chips (salary_bucket given): exactly the selected buckets match, nothing else - 'unspecified'
        is itself a selectable bucket name for a null-salary row, so picking e.g. only '60_100k' never lets an
        unpriced listing sneak back in via the unrelated unspecified toggle.
      - range slider (salary_min/salary_max, no buckets): the numeric range, OR'd with null-salary rows when
        salary_include_unspecified is true (the toggle's own documented job)."""
    lo_expr = f"COALESCE({salary.eur_year_sql('min')}, {salary.eur_year_sql('max')})"
    hi_expr = f"COALESCE({salary.eur_year_sql('max')}, {salary.eur_year_sql('min')})"
    is_null = f"({salary.eur_year_sql('min')} IS NULL AND {salary.eur_year_sql('max')} IS NULL)"

    if f.salary_bucket:
        params: Dict[str, Any] = {}
        bucket_parts = []
        for i, b in enumerate(f.salary_bucket):
            if b == "unspecified":
                bucket_parts.append(is_null)
                continue
            bounds = salary.bucket_bounds(b)
            if bounds is None:
                continue
            lo, hi = bounds
            bp = []
            if lo is not None:
                bp.append(f"{hi_expr} >= %(sb_lo_{i})s")
                params[f"sb_lo_{i}"] = lo
            if hi is not None:
                bp.append(f"{lo_expr} < %(sb_hi_{i})s")
                params[f"sb_hi_{i}"] = hi
            bucket_parts.append("(" + " AND ".join(bp) + ")" if bp else "TRUE")
        clause = "(" + " OR ".join(bucket_parts) + ")" if bucket_parts else "FALSE"
        extra = []
        if f.salary_min is not None:
            extra.append("%(salary_min)s")
            params["salary_min"] = f.salary_min
            extra[-1] = f"{hi_expr} >= %(salary_min)s"
        if f.salary_max is not None:
            extra.append(f"{lo_expr} <= %(salary_max)s")
            params["salary_max"] = f.salary_max
        if extra:
            clause = f"({clause} AND {' AND '.join(extra)})"
        return clause, params

    if f.salary_min is None and f.salary_max is None:
        return None if f.salary_include_unspecified else (f"NOT {is_null}", {})
    parts: List[str] = []
    params = {}
    if f.salary_min is not None:
        parts.append(f"{hi_expr} >= %(salary_min)s")
        params["salary_min"] = f.salary_min
    if f.salary_max is not None:
        parts.append(f"{lo_expr} <= %(salary_max)s")
        params["salary_max"] = f.salary_max
    matched = "(" + " AND ".join(parts) + ")"
    clause = f"({matched} OR {is_null})" if f.salary_include_unspecified else f"({matched} AND NOT {is_null})"
    return clause, params


_DURATION_SQL = {
    "permanent": "l.contract = 'permanent'",           # checked first: a permanent role's months, if any
                                                        # stated, never demotes it out of the 'permanent' bucket
    "lt6": "l.duration_months_v < 6",
    "6_12": "l.duration_months_v >= 6 AND l.duration_months_v < 12",
    "12_24": "l.duration_months_v >= 12 AND l.duration_months_v < 24",
    "24_36": "l.duration_months_v >= 24 AND l.duration_months_v < 36",
    "gt36": "l.duration_months_v >= 36",
    "unknown": "l.duration_months_v IS NULL AND l.contract IS DISTINCT FROM 'permanent'",
}
_DURATION_CASE_ORDER = ("permanent", "lt6", "6_12", "12_24", "24_36", "gt36", "unknown")


def _duration(f: ListingFilters) -> Clause:
    if not f.duration_bucket:
        return None
    parts = [_DURATION_SQL[b] for b in f.duration_bucket if b in _DURATION_SQL]
    return ("(" + " OR ".join(f"({p})" for p in parts) + ")", {}) if parts else None


def duration_bucket_case_sql() -> str:
    """/v1/facets' duration_bucket dimension: the same per-row bucket the ``duration`` filter matches against,
    as a single CASE expression so it can be GROUP BY'd."""
    whens = " ".join(f"WHEN {_DURATION_SQL[name]} THEN '{name}'" for name in _DURATION_CASE_ORDER)
    return f"(CASE {whens} ELSE NULL END)"


def _ft_pt(f: ListingFilters) -> Clause:
    return ("l.ft_pt = ANY(%(ft_pt)s)", {"ft_pt": f.ft_pt}) if f.ft_pt else None


_SWOW_TRUE = "(l.study_while_work_ok_v = true OR l.contract = 'phd_enrolment' OR l.ft_pt = 'part')"
_SWOW_FALSE = ("(l.study_while_work_ok_v = false AND l.contract IS DISTINCT FROM 'phd_enrolment' "
               "AND l.ft_pt IS DISTINCT FROM 'part')")
SWOW_CASE_SQL = f"(CASE WHEN {_SWOW_TRUE} THEN 'true' WHEN {_SWOW_FALSE} THEN 'false' ELSE 'unknown' END)"


def _study_while_working(f: ListingFilters) -> Clause:
    if not f.study_while_working:
        return None
    return (f"{SWOW_CASE_SQL} = ANY(%(swow)s)", {"swow": f.study_while_working})


def _kind(f: ListingFilters) -> Clause:
    return ("l.kind = ANY(%(kind)s)", {"kind": f.kind}) if f.kind else None


def _position_kind(f: ListingFilters) -> Clause:
    return ("l.position_kind = ANY(%(position_kind)s)", {"position_kind": f.position_kind}) if f.position_kind else None


def _language(f: ListingFilters) -> Clause:
    if not f.language:
        return None
    sql = "EXISTS (SELECT 1 FROM jsonb_array_elements(l.language) le WHERE le->>'lang' = ANY(%(language)s)"
    params: Dict[str, Any] = {"language": f.language}
    if f.language_level:
        sql += " AND le->>'level' ILIKE %(language_level)s"
        params["language_level"] = f"%{f.language_level}%"
    sql += ")"
    return sql, params


def _degree_required(f: ListingFilters) -> Clause:
    if not f.degree_required:
        return None
    return "COALESCE(l.degree_required, 'unknown') = ANY(%(degree_required)s)", {"degree_required": f.degree_required}


def _deadline(f: ListingFilters) -> Clause:
    parts, params = [], {}
    if f.deadline_bucket == "closing_soon":
        parts.append("l.deadline IS NOT NULL AND l.deadline <= now() + interval '14 days'")
    elif f.deadline_bucket == "this_month":
        parts.append("l.deadline IS NOT NULL AND date_trunc('month', l.deadline) = date_trunc('month', now())")
    if f.deadline_from is not None:
        parts.append("l.deadline >= %(deadline_from)s")
        params["deadline_from"] = f.deadline_from
    if f.deadline_to is not None:
        parts.append("l.deadline <= %(deadline_to)s")
        params["deadline_to"] = f.deadline_to
    return ("(" + " AND ".join(parts) + ")", params) if parts else None


def _status(f: ListingFilters) -> Clause:
    return ("l.status = ANY(%(status)s)", {"status": f.status}) if f.status else None


def _source(f: ListingFilters) -> Clause:
    if not f.source:
        return None
    return ("EXISTS (SELECT 1 FROM jsonb_array_elements(l.sources) s WHERE s->>'source' = ANY(%(source)s))",
            {"source": f.source})


def _location_class(f: ListingFilters) -> Clause:
    return ("l.location_class = ANY(%(location_class)s)", {"location_class": f.location_class}) if f.location_class else None


def _text(f: ListingFilters) -> Clause:
    if not f.text:
        return None
    needle = f"%{f.text}%"
    return ("(l.title_raw ILIKE %(text)s OR l.requirements_raw ILIKE %(text)s OR l.raw_text ILIKE %(text)s)",
            {"text": needle})


DIMENSIONS: Dict[str, Callable[[ListingFilters], Clause]] = {
    "country": _country, "city": _city, "discipline": _discipline, "foreigner": _foreigner,
    "salary": _salary, "duration": _duration, "ft_pt": _ft_pt, "study_while_working": _study_while_working,
    "kind": _kind, "position_kind": _position_kind, "language": _language, "degree_required": _degree_required,
    "deadline": _deadline, "status": _status, "source": _source, "text": _text,
    "location_class": _location_class,
}


def where_sql(f: ListingFilters, exclude: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """The AND of every active dimension's clause except ``exclude``'s (used by /v1/facets to compute a
    dimension's own counts as if it were unfiltered while every OTHER filter still applies). Returns
    ("TRUE", {}) when nothing is active - always a valid, always-true fragment to splice into a WHERE."""
    sql_parts, params = [], {}
    for i, (name, fn) in enumerate(DIMENSIONS.items()):
        if name == exclude:
            continue
        clause = fn(f)
        if clause is None:
            continue
        frag, p = clause
        renamed = {f"{k}__{i}": v for k, v in p.items()}
        for old in p:
            frag = frag.replace(f"%({old})s", f"%({old}__{i})s")
        sql_parts.append(frag)
        params.update(renamed)
    return (" AND ".join(sql_parts) if sql_parts else "TRUE"), params
