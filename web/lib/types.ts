// Mirrors src/ajp/api/schemas.py exactly - one place to keep the two in sync. Every uncertain field keeps
// its {v, conf, raw} shape end to end (D6); the UI never collapses it to a bare value before showing it.

export interface Confident<T = unknown> {
  v: T | null;
  conf: number | null;
  raw: string | null;
}

export interface SourceRef {
  source_id: string;
  url: string;
  first_seen: string | null;
  last_seen: string | null;
  verified_direct: boolean;
}

export interface SalaryBasis {
  min_eur_year: number | null;
  max_eur_year: number | null;
  fx_rate_date: string | null;
  fx_source: string | null;
  note: string | null;
}

export interface OrgRef {
  id: string | null;
  slug: string | null;
  name_en: string | null;
  name_native: string | null;
  homepage: string | null;
}

export interface ListingCard {
  id: string;
  title_raw: string;
  org_raw: string | null;
  org: OrgRef | null;
  country_iso2: string | null;
  city: string | null;
  location_class: string;
  location_class_conf: number | null;
  location_class_raw: string | null;
  lat: number | null;
  lon: number | null;
  kind: string;
  position_kind: string;
  ft_pt: string;
  contract: string | null;
  salary_currency: string | null;
  salary_period: string | null;
  salary_eur_year: SalaryBasis;
  foreigner_eligible: string;
  deadline: string | null;
  posted_at: string | null;
  status: string;
  sources: SourceRef[];
  canonical_source_id: string | null;
  verified_direct: boolean;
}

export interface ListingDetail extends ListingCard {
  url: string;
  disciplines_euroscivoc: string[];
  rs_research_title: string | null;
  duration_months: Confident<number>;
  hours_pct: number | null;
  study_while_work_ok: Confident<boolean>;
  salary_min: number | null;
  salary_max: number | null;
  salary_gross_net: string | null;
  salary_raw: string | null;
  degree_required: string | null;
  language: { lang: string; level?: string; required?: boolean }[];
  requirements_raw: string | null;
  contact_emails: string[];
  contact_phones: string[];
  contact_name: string | null;
  contact_scope: string | null;
  apply_url: string | null;
  ad_lang: string | null;
  raw_text: string | null;
  extraction_family: string | null;
  content_scanned: boolean;
  posted_at_basis: string | null;
  posted_at_conf: number | null;
  posted_at_raw: string | null;
  first_seen: string;
  last_seen: string;
}

export interface Page {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ListingsResponse {
  page: Page;
  sort: string;
  results: ListingCard[];
  degraded: string[];
}

export interface FacetBucket {
  value: string;
  label: string | null;
  count: number;
}

export interface FacetsResponse {
  country: FacetBucket[];
  city: FacetBucket[];
  discipline: FacetBucket[];
  kind: FacetBucket[];
  position_kind: FacetBucket[];
  ft_pt: FacetBucket[];
  degree_required: FacetBucket[];
  language: FacetBucket[];
  source: FacetBucket[];
  foreigner: FacetBucket[];
  duration_bucket: FacetBucket[];
  salary_bucket: FacetBucket[];
  location_class: FacetBucket[];
}

export interface SourceStatus {
  id: string;
  kind: string;
  route_type: string;
  robots_ok: boolean | null;
  last_run_status: string;
  last_attempt_at: string | null;
  last_success_at: string | null;
  listing_count: number;
  degraded_reasons: string[];
  ats_family: string | null;
  ats_tenant: string | null;
  ats_site: string | null;
  tdm_reservation: string;
  geo_scope: string | null;
  requests_24h: number;
  denied_24h: number;
}

// Mirrors ajp.discovery.governance_snapshot()'s own return shape exactly (src/ajp/api/routers/governance.py
// returns it as a bare dict, never through a pydantic response_model - see test_openapi_snapshot.py's own
// named exception list for why).
export interface GovernanceSnapshot {
  discovery_funnel: { discovered: number; dry_run: number; active: number };
  discovery_method: { href_harvest: number; fixed_path_backfill: number };
  per_host_last_24h: { host: string; runs_24h: number; paths_fetched_24h: number; denied_24h: number }[];
  robots_denied_24h_total: number;
}

export interface OrgSummary {
  id: string;
  slug: string | null;
  name_en: string | null;
  name_native: string | null;
  country: string;
  city: string | null;
  type: string;
  homepage: string | null;
  open_listing_count: number;
}

export interface OrgDetail extends OrgSummary {
  career_urls: string[];
  render_mode: string | null;
  robots_status: string | null;
  robots_checked_at: string | null;
  openalex_id: string | null;
  openalex_works_count: number | null;
  qs_rank: number | null;
  the_rank: number | null;
}

export interface ChannelLinkOut {
  source_id: string;
  slug: string;
  label: string;
  url: string;
  country_iso2: string | null;
  description: string | null;
}

export interface HealthResponse {
  status: string;
  db: string;
  fx_as_of: string | null;
  fx_currencies: number;
  sources_last_run: Record<string, unknown>[];
  degraded_sources: string[];
}

export interface ThesisFinding {
  check: string;
  severity: "pass" | "warn" | "fail";
  message: string;
  page: number | null;
  excerpt: string | null;
  conf: number | null;
}

export interface ThesisReport {
  template_name: string;
  summary: { pass: number; warn: number; fail: number };
  findings: ThesisFinding[];
}

export interface SpanMatch {
  sentence_index: number;
  start: number;
  end: number;
  token_count: number;
  snippet: string;
  source_id: string;
  signal: "verbatim" | "paraphrase";
  score: number;
  source_url: string | null;
  title: string | null;
  attribution: string | null;
}

export interface SourceCoverage {
  source_id: string;
  available: boolean;
  candidates_checked: number;
  matched: number;
  error: string | null;
}

export interface SimilarityReport {
  total_sentences: number;
  total_tokens: number;
  verbatim_oab_pct: number;
  verbatim_internal_pct: number;
  union_verbatim_pct: number;
  sentence_exact_pct: number;
  estimate_pct: number;
  confidence_band: "very_low" | "low" | "medium" | "high";
  coverage_statement: string;
  coverage: SourceCoverage[];
  matched_spans: SpanMatch[];
  paraphrase_spans: SpanMatch[];
  copyleaks: Record<string, unknown> | null;
  disclaimers: string[];
}

export interface ThesisTask {
  id: string;
  status: "processing" | "done" | "failed";
  error: string | null;
  original_filename: string;
  source_format: string;
  template_name: string;
  report: ThesisReport | null;
  similarity: Record<string, unknown> | null;
  created_at: string;
}

// T25: applicant CV upload + matching
export interface ApplicantProfile {
  disciplines?: string[];
  degree?: { v: string; raw: string } | null;
  skills?: string[];
  languages?: { lang: string; level: string | null }[];
  desired_countries?: string[];
  desired_cities?: string[];
  remote_ok?: boolean | null;
  ft_pt?: "full" | "part" | null;
  years_experience?: number | null;
  min_salary_eur_year?: number | null;
  min_salary_raw?: string;
  foreigner_visa_note?: string | null;
}

export interface Applicant {
  id: string;
  status: "processing" | "done" | "failed";
  error: string | null;
  cv_filename: string;
  source_format: string;
  profile: ApplicantProfile;
  created_at: string;
}

export interface MatchFactor {
  weight: number;
  fraction: number;
  points: number;
  reason: string;
  state: "positive" | "negative" | "unknown"; // T33 (packet 018) - neutral-unknown scoring ruling
}

export interface ApplicantMatch {
  listing_id: string;
  score: number;
  factors: Record<string, MatchFactor>;
  counts: { positive: number; negative: number; unknown: number };
  coverage: number; // weight backed by positive evidence / total_weight (T33)
  total_weight: number;
  title_raw: string | null;
  org_raw: string | null;
  url: string | null;
  apply_url: string | null;
  contact_emails: string[];
  contact_phones: string[];
  country_iso2: string | null;
  city: string | null;
  location_class: string | null;
  computed_at: string | null;
}

export interface ApplicantMatchesResponse {
  total: number;
  results: ApplicantMatch[];
}

export interface MetaResponse {
  disciplines_euroscivoc: { code: string; n: number }[];
  disciplines_taxonomy_complete: boolean;
  rs_research_title: string[];
  position_kind: string[];
  kind: string[];
  degree_required: string[];
  ft_pt: string[];
  currencies: string[];
  fx_as_of: string | null;
  language_codes: string[];
  salary_buckets: { name: string; min_eur_year: number | null; max_eur_year: number | null }[];
  duration_buckets: string[];
  degraded_features: { feature: string; reason: string }[];
}
