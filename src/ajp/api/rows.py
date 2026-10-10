"""Shared SELECT skeleton + DB-row -> pydantic-model mapping for /v1/listings, /v1/listings/{id} and
/v1/facets (facets reuses BASE_SQL's FROM/JOIN so a bucket's count is computed over the exact same
per-listing EUR/year figure a listing card shows).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ajp.api import grouping, salary
from ajp.api.schemas import ListingCard, ListingDetail, OrgRef, SalaryBasis

BASE_SELECT = f"""
    l.*,
    o.id AS org_id_full, o.slug AS org_slug, o.name_en AS org_name_en,
    o.name_native AS org_name_native, o.homepage AS org_homepage,
    {salary.eur_year_sql('min')} AS salary_eur_year_min,
    {salary.eur_year_sql('max')} AS salary_eur_year_max,
    fx.rate_date AS fx_rate_date, fx.source AS fx_source
"""
BASE_FROM = """
    FROM listing l
    LEFT JOIN org_master o ON o.id = l.org_id
    LEFT JOIN fx_rate_latest fx ON fx.currency = l.salary_currency AND l.salary_currency <> 'EUR'
"""


def _salary_note(row: Dict[str, Any]) -> Optional[str]:
    period = row.get("salary_period")
    if period == "hour":
        return "annualized assuming 40h/week x 52 weeks/year (ad states an hourly rate)"
    if period == "stipend":
        if row.get("duration_months_v") is None:
            return "stipend stated but no contract duration to annualize it over - EUR/year left unknown"
        return "stipend annualized over the ad's own stated duration (amount / duration_months x 12)"
    if row.get("salary_currency") and row["salary_currency"] != "EUR" and row.get("fx_rate_date") is None:
        return f"no FX rate on file for {row['salary_currency']} - EUR/year left unknown"
    return None


def _salary_basis(row: Dict[str, Any]) -> SalaryBasis:
    return SalaryBasis(
        min_eur_year=row.get("salary_eur_year_min"), max_eur_year=row.get("salary_eur_year_max"),
        fx_rate_date=row.get("fx_rate_date"), fx_source=row.get("fx_source"), note=_salary_note(row),
    )


def _org_ref(row: Dict[str, Any]) -> Optional[OrgRef]:
    if row.get("org_id_full") is None:
        return None
    return OrgRef(id=str(row["org_id_full"]), slug=row.get("org_slug"), name_en=row.get("org_name_en"),
                  name_native=row.get("org_name_native"), homepage=row.get("org_homepage"))


def _sources_and_canonical(row: Dict[str, Any]):
    formatted = grouping.format_sources(row.get("sources") or [])
    canon = grouping.canonical_source(row.get("sources") or [])
    canon_id = canon.get("source") if canon else None
    verified = grouping.is_verified_direct(canon_id) if canon_id else False
    return formatted, canon_id, verified


def row_to_card(row: Dict[str, Any]) -> ListingCard:
    sources, canon_id, verified = _sources_and_canonical(row)
    return ListingCard(
        id=str(row["id"]), title_raw=row["title_raw"], org_raw=row.get("org_raw"), org=_org_ref(row),
        country_iso2=row.get("country_iso2"), city=row.get("city"), lat=row.get("lat"), lon=row.get("lon"),
        location_class=row.get("location_class") or "unknown", location_class_conf=row.get("location_class_conf"),
        location_class_raw=row.get("location_class_raw"),
        kind=row["kind"], position_kind=row["position_kind"], ft_pt=row["ft_pt"], contract=row.get("contract"),
        salary_currency=row.get("salary_currency"), salary_period=row.get("salary_period"),
        salary_eur_year=_salary_basis(row), foreigner_eligible=row["foreigner_eligible"],
        deadline=row.get("deadline"), posted_at=row.get("posted_at"), status=row["status"],
        sources=sources, canonical_source_id=canon_id, verified_direct=verified,
    )


def row_to_detail(row: Dict[str, Any]) -> ListingDetail:
    card = row_to_card(row)
    return ListingDetail(
        **card.model_dump(), url=row["url"], disciplines_euroscivoc=row.get("disciplines_euroscivoc") or [],
        rs_research_title=row.get("rs_research_title"),
        duration_months={"v": row.get("duration_months_v"), "conf": row.get("duration_months_conf"),
                          "raw": row.get("duration_months_raw")},
        hours_pct=row.get("hours_pct"),
        study_while_work_ok={"v": row.get("study_while_work_ok_v"), "conf": row.get("study_while_work_ok_conf"),
                              "raw": row.get("study_while_work_ok_raw")},
        salary_min=row.get("salary_min"), salary_max=row.get("salary_max"),
        salary_gross_net=row.get("salary_gross_net"), salary_raw=row.get("salary_raw"),
        degree_required=row.get("degree_required"), language=row.get("language") or [],
        requirements_raw=row.get("requirements_raw"), contact_emails=row.get("contact_emails") or [],
        contact_phones=row.get("contact_phones") or [], contact_name=row.get("contact_name"),
        contact_scope=row.get("contact_scope"), apply_url=row.get("apply_url"), ad_lang=row.get("ad_lang"),
        raw_text=row.get("raw_text"), extraction_family=row.get("extraction_family"),
        content_scanned=row.get("content_scanned") or False,
        posted_at_basis=row.get("posted_at_basis"), posted_at_conf=row.get("posted_at_conf"),
        posted_at_raw=row.get("posted_at_raw"), first_seen=row["first_seen"], last_seen=row["last_seen"],
    )
