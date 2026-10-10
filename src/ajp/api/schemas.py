"""Pydantic response models (packet 007: 'pydantic response models'). Every uncertain field keeps its
{v, conf, raw} shape end to end (D6) rather than collapsing to a bare value - the UI's confidence markers
and raw-excerpt reveal (T15) read these three sub-fields directly.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class Confident(BaseModel):
    v: Optional[Any] = None
    conf: Optional[float] = None
    raw: Optional[str] = None


class SourceRef(BaseModel):
    source_id: str
    url: str
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    verified_direct: bool = False


class SalaryBasis(BaseModel):
    min_eur_year: Optional[float] = None
    max_eur_year: Optional[float] = None
    fx_rate_date: Optional[date] = None
    fx_source: Optional[str] = None
    note: Optional[str] = None


class OrgRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: Optional[str] = None
    slug: Optional[str] = None
    name_en: Optional[str] = None
    name_native: Optional[str] = None
    homepage: Optional[str] = None


class ListingCard(BaseModel):
    id: str
    title_raw: str
    org_raw: Optional[str] = None
    org: Optional[OrgRef] = None
    country_iso2: Optional[str] = None            # T23 (PM ruling R2): nullable - see location_class
    city: Optional[str] = None
    location_class: str = "unknown"               # located|remote|hybrid|unknown (migration 0020)
    location_class_conf: Optional[float] = None
    location_class_raw: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    kind: str
    position_kind: str
    ft_pt: str
    contract: Optional[str] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None
    salary_eur_year: SalaryBasis
    foreigner_eligible: str
    deadline: Optional[datetime] = None
    posted_at: Optional[datetime] = None
    status: str
    sources: List[SourceRef]
    canonical_source_id: Optional[str] = None
    verified_direct: bool = False


class ListingDetail(ListingCard):
    url: str
    disciplines_euroscivoc: List[str] = []
    rs_research_title: Optional[str] = None
    duration_months: Confident
    hours_pct: Optional[float] = None
    study_while_work_ok: Confident
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_gross_net: Optional[str] = None
    salary_raw: Optional[str] = None
    degree_required: Optional[str] = None
    language: List[Dict[str, Any]] = []
    requirements_raw: Optional[str] = None
    contact_emails: List[str] = []
    contact_phones: List[str] = []
    contact_name: Optional[str] = None
    contact_scope: Optional[str] = None
    apply_url: Optional[str] = None
    ad_lang: Optional[str] = None
    raw_text: Optional[str] = None
    extraction_family: Optional[str] = None
    content_scanned: bool = False
    posted_at_basis: Optional[str] = None
    posted_at_conf: Optional[float] = None
    posted_at_raw: Optional[str] = None
    first_seen: datetime
    last_seen: datetime


class Page(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class ListingsResponse(BaseModel):
    page: Page
    sort: str
    results: List[ListingCard]
    degraded: List[str] = []           # e.g. discipline-hierarchy-not-loaded, ft_pt-etc; see meta.degraded_features


class FacetBucket(BaseModel):
    value: str
    label: Optional[str] = None
    count: int


class FacetsResponse(BaseModel):
    country: List[FacetBucket] = []
    city: List[FacetBucket] = []
    discipline: List[FacetBucket] = []
    kind: List[FacetBucket] = []
    position_kind: List[FacetBucket] = []
    ft_pt: List[FacetBucket] = []
    degree_required: List[FacetBucket] = []
    language: List[FacetBucket] = []
    source: List[FacetBucket] = []
    foreigner: List[FacetBucket] = []
    duration_bucket: List[FacetBucket] = []
    salary_bucket: List[FacetBucket] = []
    location_class: List[FacetBucket] = []        # T23 (PM ruling R2): 'Remote/Unknown' facet


class SourceStatus(BaseModel):
    id: str
    kind: str
    route_type: str
    robots_ok: Optional[bool]
    last_run_status: str
    last_attempt_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    listing_count: int
    degraded_reasons: List[str] = []
    # T18: detected ATS family/tenant/site + TDM reservation state, persisted on the source row itself.
    ats_family: Optional[str] = None
    ats_tenant: Optional[str] = None
    ats_site: Optional[str] = None
    tdm_reservation: str = "none"
    geo_scope: Optional[str] = None
    requests_24h: int = 0     # T24 compliance dashboard: real, from run_ledger.pages_fetched, never estimated
    denied_24h: int = 0       # a real RobotsDenied/ManifestViolation in the last 24h - must be 0 by construction


class OrgSummary(BaseModel):
    id: str
    slug: Optional[str] = None
    name_en: Optional[str] = None
    name_native: Optional[str] = None
    country: str
    city: Optional[str] = None
    type: str
    homepage: Optional[str] = None
    open_listing_count: int


class OrgDetail(OrgSummary):
    career_urls: List[str] = []
    render_mode: Optional[str] = None
    robots_status: Optional[str] = None
    robots_checked_at: Optional[datetime] = None
    openalex_id: Optional[str] = None
    openalex_works_count: Optional[int] = None
    qs_rank: Optional[int] = None
    the_rank: Optional[int] = None


class ChannelLinkOut(BaseModel):
    source_id: str
    slug: str
    label: str
    url: str
    country_iso2: Optional[str] = None
    description: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    db: str
    fx_as_of: Optional[date] = None
    fx_currencies: int = 0
    sources_last_run: List[Dict[str, Any]] = []
    degraded_sources: List[str] = []
    fx_stale_warning: Optional[str] = None


class ThesisFinding(BaseModel):
    check: str
    severity: str
    message: str
    page: Optional[int] = None
    excerpt: Optional[str] = None
    conf: Optional[float] = None


class ThesisReport(BaseModel):
    template_name: str
    summary: Dict[str, int]
    findings: List[ThesisFinding]


class ThesisTaskOut(BaseModel):
    id: str
    status: str
    error: Optional[str] = None
    original_filename: str
    source_format: str
    template_name: str
    report: Optional[ThesisReport] = None
    similarity: Optional[Dict[str, Any]] = None
    created_at: datetime


class MetaResponse(BaseModel):
    disciplines_euroscivoc: List[Dict[str, Any]]
    disciplines_taxonomy_complete: bool
    rs_research_title: List[str]
    position_kind: List[str]
    kind: List[str]
    degree_required: List[str]
    ft_pt: List[str]
    currencies: List[str]
    fx_as_of: Optional[date] = None
    language_codes: List[str]
    salary_buckets: List[Dict[str, Any]]
    duration_buckets: List[str]
    degraded_features: List[Dict[str, str]]


# ---------------------------------------------------------------- T25: applicants -----------------------------
class ApplicantOut(BaseModel):
    id: str
    status: str
    error: Optional[str] = None
    cv_filename: str
    source_format: str
    profile: Dict[str, Any]
    created_at: datetime


class MatchFactor(BaseModel):
    weight: int
    fraction: float
    points: float
    reason: str
    state: str          # 'positive' | 'negative' | 'unknown' (T33, packet 018 - neutral-unknown ruling)


class ApplicantMatchOut(BaseModel):
    listing_id: str
    score: float
    factors: Dict[str, MatchFactor]
    counts: Dict[str, int]     # {'positive': n, 'negative': n, 'unknown': n} - always sums to len(factors)
    coverage: float           # weight backed by POSITIVE evidence / total_weight - never mistake a sparse
                              # ~50 (many 'unknown's) for a genuinely well-evidenced match (T33)
    total_weight: int
    title_raw: Optional[str] = None
    org_raw: Optional[str] = None
    url: Optional[str] = None
    apply_url: Optional[str] = None
    contact_emails: List[str] = []
    contact_phones: List[str] = []
    country_iso2: Optional[str] = None
    city: Optional[str] = None
    location_class: Optional[str] = None
    computed_at: Optional[datetime] = None


class ApplicantMatchesResponse(BaseModel):
    total: int
    results: List[ApplicantMatchOut]
