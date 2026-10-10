// Fixture API responses (T15 acceptance: "render test per major page with MSW or fixture API" - fixture route).
import type {
  ChannelLinkOut, FacetsResponse, GovernanceSnapshot, ListingDetail, ListingsResponse, MetaResponse, OrgDetail,
  OrgSummary, SourceStatus,
} from "@/lib/types";

export const FIXTURE_LISTING: ListingDetail = {
  id: "11111111-1111-1111-1111-111111111111",
  title_raw: "PhD Position in Robotics",
  org_raw: "Demo University",
  org: { id: "org-1", slug: "demo-uni", name_en: "Demo University", name_native: null, homepage: "https://demo.invalid/" },
  country_iso2: "RS", city: "Novi Sad", location_class: "located", location_class_conf: null, location_class_raw: null,
  lat: 45.2671, lon: 19.8335,
  kind: "employment", position_kind: "phd", ft_pt: "full", contract: "fixed",
  salary_currency: "EUR", salary_period: "month",
  salary_eur_year: { min_eur_year: 36000, max_eur_year: 36000, fx_rate_date: null, fx_source: null, note: null },
  foreigner_eligible: "unknown", deadline: "2026-12-01T00:00:00Z", posted_at: "2026-09-01T00:00:00Z", status: "open",
  sources: [
    { source_id: "rs_org_boards", url: "https://demo.invalid/jobs/1", first_seen: "2026-09-01T00:00:00Z", last_seen: "2026-09-01T00:00:00Z", verified_direct: true },
    { source_id: "sljaka", url: "https://sljaka.invalid/jobs/1", first_seen: "2026-09-02T00:00:00Z", last_seen: "2026-09-02T00:00:00Z", verified_direct: false },
    { source_id: "academictransfer", url: "https://academictransfer.invalid/jobs/1", first_seen: "2026-09-03T00:00:00Z", last_seen: "2026-09-03T00:00:00Z", verified_direct: false },
  ],
  canonical_source_id: "rs_org_boards", verified_direct: true,
  url: "https://demo.invalid/jobs/1", disciplines_euroscivoc: ["1.2_computer_and_information_sciences"],
  rs_research_title: null,
  duration_months: { v: 12, conf: 0.9, raw: "12 months" },
  hours_pct: null,
  study_while_work_ok: { v: null, conf: null, raw: null },
  salary_min: 3000, salary_max: 3000, salary_gross_net: "gross", salary_raw: "3000 EUR/month",
  degree_required: "phd", language: [{ lang: "en", level: "C1", required: true }],
  requirements_raw: "MSc in a relevant field.", contact_emails: ["hr@demo.invalid"], contact_phones: [],
  contact_name: null, contact_scope: "ad", apply_url: "https://demo.invalid/apply/1", ad_lang: "en",
  raw_text: null, extraction_family: "article", content_scanned: false, posted_at_basis: null, posted_at_conf: null, posted_at_raw: null,
  first_seen: "2026-09-01T00:00:00Z", last_seen: "2026-09-03T00:00:00Z",
};

export const FIXTURE_LISTINGS: ListingsResponse = {
  page: { page: 1, page_size: 20, total: 1, total_pages: 1 },
  sort: "posted_desc",
  results: [FIXTURE_LISTING],
  degraded: [],
};

export const FIXTURE_FACETS: FacetsResponse = {
  country: [{ value: "RS", label: null, count: 1 }],
  city: [{ value: "Novi Sad", label: null, count: 1 }],
  discipline: [{ value: "1.2_computer_and_information_sciences", label: null, count: 1 }],
  kind: [{ value: "employment", label: null, count: 1 }, { value: "enrollment", label: null, count: 0 }],
  position_kind: [{ value: "phd", label: null, count: 1 }],
  ft_pt: [{ value: "full", label: null, count: 1 }],
  degree_required: [{ value: "phd", label: null, count: 1 }],
  language: [{ value: "en", label: null, count: 1 }],
  source: [{ value: "rs_org_boards", label: null, count: 1 }],
  foreigner: [{ value: "unknown", label: null, count: 1 }],
  duration_bucket: [{ value: "12_24", label: null, count: 1 }],
  salary_bucket: [{ value: "25_40k", label: null, count: 1 }],
  location_class: [{ value: "located", label: null, count: 1 }],
};

export const FIXTURE_META: MetaResponse = {
  disciplines_euroscivoc: [{ code: "1.2_computer_and_information_sciences", n: 1 }],
  disciplines_taxonomy_complete: false,
  rs_research_title: ["naucni_saradnik"],
  position_kind: ["phd", "postdoc", "professor"],
  kind: ["employment", "enrollment", "grant_call", "internship", "other"],
  degree_required: ["bsc", "msc", "phd", "none", "unknown"],
  ft_pt: ["full", "part", "both", "unknown"],
  currencies: ["EUR", "GBP"],
  fx_as_of: "2026-09-22",
  language_codes: ["en", "sr"],
  salary_buckets: [{ name: "25_40k", min_eur_year: 25000, max_eur_year: 40000 }],
  duration_buckets: ["lt6", "6_12", "12_24", "24_36", "gt36", "permanent", "unknown"],
  degraded_features: [{ feature: "discipline_hierarchy", reason: "no EuroSciVoc tree loaded yet" }],
};

export const FIXTURE_CHANNELS: ChannelLinkOut[] = [
  { source_id: "euraxess", slug: "serbia-jobs", label: "Serbia: research jobs on EURAXESS", url: "https://euraxess.ec.europa.eu/jobs/search?f%5B0%5D=job_country%3A786", country_iso2: "RS", description: "Opens on EURAXESS." },
];

export const FIXTURE_SOURCES: SourceStatus[] = [
  { id: "rs_org_boards", kind: "scrape_html", route_type: "direct_org", robots_ok: true, last_run_status: "ok",
    last_attempt_at: "2026-09-24T06:00:00Z", last_success_at: "2026-09-24T06:00:00Z", listing_count: 42,
    degraded_reasons: [], ats_family: null, ats_tenant: null, ats_site: null, tdm_reservation: "none",
    geo_scope: "RS", requests_24h: 120, denied_24h: 0 },
  { id: "sljaka", kind: "feed", route_type: "aggregator", robots_ok: true, last_run_status: "degraded",
    last_attempt_at: "2026-09-24T05:00:00Z", last_success_at: "2026-09-23T05:00:00Z", listing_count: 10,
    degraded_reasons: ["unreachable_after_3_runs: timeout"], ats_family: null, ats_tenant: null, ats_site: null,
    tdm_reservation: "none", geo_scope: "INTL", requests_24h: 3, denied_24h: 0 },
  { id: "euraxess", kind: "channel", route_type: "deeplink", robots_ok: null, last_run_status: "not_applicable",
    last_attempt_at: null, last_success_at: null, listing_count: 0, degraded_reasons: [], ats_family: null,
    ats_tenant: null, ats_site: null, tdm_reservation: "none", geo_scope: "RS", requests_24h: 0, denied_24h: 0 },
];

export const FIXTURE_GOVERNANCE: GovernanceSnapshot = {
  discovery_funnel: { discovered: 5, dry_run: 2, active: 1 },
  discovery_method: { href_harvest: 4, fixed_path_backfill: 4 },
  per_host_last_24h: [{ host: "example.ac.rs", runs_24h: 1, paths_fetched_24h: 3, denied_24h: 0 }],
  robots_denied_24h_total: 0,
};

export const FIXTURE_ORGS: OrgSummary[] = [
  { id: "org-1", slug: "demo-uni", name_en: "Demo University", name_native: null, country: "RS", city: "Novi Sad", type: "university", homepage: "https://demo.invalid/", open_listing_count: 3 },
];

export const FIXTURE_ORG_DETAIL: OrgDetail = {
  ...FIXTURE_ORGS[0], career_urls: ["https://demo.invalid/jobs"], render_mode: "httpx", robots_status: "allowed",
  robots_checked_at: "2026-09-01T00:00:00Z", openalex_id: "I123", openalex_works_count: 500, qs_rank: null, the_rank: null,
};

/** A route value wrapped this way lets a fixture route answer with a non-200 status (e.g. a deliberate 404) -
 * every other route value is still sent back as plain 200 JSON, unchanged. */
export function statusResponse(status: number, body: unknown = { detail: "not found in fixture" }) {
  return { __mockStatus: status, __mockBody: body };
}

function isStatusResponse(v: unknown): v is { __mockStatus: number; __mockBody: unknown } {
  return typeof v === "object" && v !== null && "__mockStatus" in v;
}

/** Installs a `global.fetch` stub that resolves each API path to its fixture. Restore with `restoreFetch()`.
 * Routes are checked in the order given - list more specific paths before a shorter path they contain as a
 * substring (matching is `url.includes(path)`, not an exact match). */
export function mockFetch(routes: Record<string, unknown>) {
  const real = global.fetch;
  global.fetch = (async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    for (const [path, body] of Object.entries(routes)) {
      if (url.includes(path)) {
        const [status, payload] = isStatusResponse(body) ? [body.__mockStatus, body.__mockBody] : [200, body];
        return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
      }
    }
    return new Response(JSON.stringify({ detail: "not found in fixture" }), { status: 404 });
  }) as typeof fetch;
  return () => {
    global.fetch = real;
  };
}
