"""One shared FastAPI dependency building ``ListingFilters`` from query params - used by /v1/listings and
/v1/facets so a facet's "all other filters" baseline is guaranteed identical to what /v1/listings itself
would apply. ``status`` gets its packet-mandated default here ('open' only, expired excluded by default) -
passing an explicit, even empty-seeming, value always overrides the default; omitting the param is what
triggers it.

T33 (packet 018, Q6): ``kind`` no longer defaults to 'employment' only - PM's own real-DB audit found this
hid 1,280 of 3,948 open listings (grant_call, internship, enrollment, and 'other') from every fresh visitor,
who saw an apparently near-empty site. Every real kind is now included unless the caller explicitly narrows
it - the same 'omitting the param means unfiltered' rule ``location_class`` already used (T23, PM ruling R2).
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import Query

from ajp.api.filters import ListingFilters


def listing_filters(
    country: Optional[List[str]] = Query(None, description="country_iso2, multi"),
    city: Optional[List[str]] = Query(None),
    lat: Optional[float] = Query(None, description="with lon+r_km: radius search"),
    lon: Optional[float] = Query(None),
    r_km: Optional[float] = Query(None, gt=0),
    discipline: Optional[List[str]] = Query(None, description="EuroSciVoc codes, flat match (see /v1/meta)"),
    foreigner: Optional[List[str]] = Query(None, description="yes|no|unknown"),
    salary_min: Optional[float] = Query(None, description="EUR/year"),
    salary_max: Optional[float] = Query(None, description="EUR/year"),
    salary_bucket: Optional[List[str]] = Query(None),
    salary_include_unspecified: bool = Query(True, description="keep listings with no stated salary"),
    duration_bucket: Optional[List[str]] = Query(None, description="lt6|6_12|12_24|24_36|gt36|permanent|unknown"),
    ft_pt: Optional[List[str]] = Query(None, description="full|part|both|unknown"),
    study_while_working: Optional[List[str]] = Query(None, description="true|false|unknown"),
    kind: Optional[List[str]] = Query(None, description="employment|enrollment|grant_call|internship|other; unfiltered (all kinds) by default"),
    position_kind: Optional[List[str]] = Query(None),
    language: Optional[List[str]] = Query(None),
    language_level: Optional[str] = Query(None),
    degree_required: Optional[List[str]] = Query(None, description="bsc|msc|phd|none|unknown"),
    deadline_bucket: Optional[str] = Query(None, description="closing_soon (next 14 days)|this_month (rest of this month); upcoming deadlines only"),
    deadline_from: Optional[date] = Query(None),
    deadline_to: Optional[date] = Query(None),
    status: Optional[List[str]] = Query(None, description="open|closed|expired|unknown; default open only"),
    source: Optional[List[str]] = Query(None, description="source ids among a listing's sources[]"),
    text: Optional[str] = Query(None, description="title/requirements/raw text search"),
    location_class: Optional[List[str]] = Query(
        None, description="located|remote|hybrid|unknown; unfiltered by default (T23, PM ruling R2)"),
) -> ListingFilters:
    return ListingFilters(
        country=country, city=city, lat=lat, lon=lon, r_km=r_km, discipline=discipline, foreigner=foreigner,
        salary_min=salary_min, salary_max=salary_max, salary_bucket=salary_bucket,
        salary_include_unspecified=salary_include_unspecified, duration_bucket=duration_bucket, ft_pt=ft_pt,
        study_while_working=study_while_working, kind=kind, position_kind=position_kind,
        language=language, language_level=language_level, degree_required=degree_required,
        deadline_bucket=deadline_bucket, deadline_from=deadline_from, deadline_to=deadline_to,
        status=status or ["open"], source=source, text=text, location_class=location_class,
    )
