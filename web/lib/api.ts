// T15: talks only to T14 over HTTP (configurable API base), no direct DB. NEXT_PUBLIC_API_BASE is a plain
// localhost URL, never a secret - nothing in this file (or anywhere in web/) embeds an API key, since the
// read-only listing API does not require one; see web/tests/no-secrets.test.ts for the guard that checks this
// stays true as the app grows.
import type {
  Applicant, ApplicantMatchesResponse, ChannelLinkOut, ClassificationReviewReason,
  ClassificationReviewResponse, FacetsResponse, GovernanceSnapshot, HealthResponse, ListingDetail,
  ListingsResponse, MetaResponse, OrgDetail, OrgSummary, SimilarityReport, SourceStatus, ThesisTask,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function getJson<T>(path: string, params?: URLSearchParams): Promise<T> {
  const qs = params && params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(`${API_BASE}${path}${qs}`, { cache: "no-store" });
  if (!res.ok) {
    throw new ApiError(res.status, `${path} -> ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => getJson<HealthResponse>("/health"),
  listings: (params: URLSearchParams) => getJson<ListingsResponse>("/v1/listings", params),
  listing: (id: string) => getJson<ListingDetail>(`/v1/listings/${id}`),
  facets: (params: URLSearchParams) => getJson<FacetsResponse>("/v1/facets", params),
  orgs: (country?: string) => getJson<OrgSummary[]>("/v1/orgs", country ? new URLSearchParams({ country }) : undefined),
  org: (id: string) => getJson<OrgDetail>(`/v1/orgs/${id}`),
  meta: () => getJson<MetaResponse>("/v1/meta"),
  channels: () => getJson<ChannelLinkOut[]>("/v1/channels"),
  sources: () => getJson<SourceStatus[]>("/v1/sources"),
  // T48: the classifier's own review queue. READ-only by design - recording a decision needs
  // AJP_ADMIN_TOKEN, which a browser cannot hold (see ajp.api.deps.require_admin), so this UI shows
  // the worklist and the operator acts on it with a token.
  classificationReview: (reason?: ClassificationReviewReason) =>
    getJson<ClassificationReviewResponse>("/v1/classification-review",
      reason ? new URLSearchParams({ reason }) : undefined),
  governance: () => getJson<GovernanceSnapshot>("/v1/governance"),
  exportUrl: (params: URLSearchParams, format: "csv" | "json") => {
    const p = new URLSearchParams(params);
    p.set("format", format);
    return `${API_BASE}/v1/listings/export?${p.toString()}`;
  },

  thesisTemplates: () => getJson<string[]>("/v1/thesis/templates"),
  thesisTask: (id: string) => getJson<ThesisTask>(`/v1/thesis/${id}`),
  analyzeThesis: async (file: File, template: string): Promise<ThesisTask> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/v1/thesis/analyze?template=${encodeURIComponent(template)}`, {
      method: "POST", body: form,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new ApiError(res.status, detail.detail || `analyze -> ${res.status}`);
    }
    return res.json();
  },
  thesisReportPdfUrl: (id: string) => `${API_BASE}/v1/thesis/${id}/report?format=pdf`,
  thesisExportDocxUrl: (id: string) => `${API_BASE}/v1/thesis/${id}/export`,
  deleteThesis: async (id: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/v1/thesis/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      throw new ApiError(res.status, `delete -> ${res.status}`);
    }
  },

  similaritySources: () => getJson<string[]>("/v1/thesis/similarity/sources"),
  getSimilarity: (taskId: string) => getJson<SimilarityReport>(`/v1/thesis/${taskId}/similarity`),
  runSimilarity: async (taskId: string, sources: string[], copyleaksConsent: boolean): Promise<SimilarityReport> => {
    const params = new URLSearchParams();
    for (const s of sources) params.append("sources", s);
    params.set("copyleaks_consent", String(copyleaksConsent));
    const res = await fetch(`${API_BASE}/v1/thesis/${taskId}/similarity?${params.toString()}`, { method: "POST" });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new ApiError(res.status, detail.detail || `similarity -> ${res.status}`);
    }
    return res.json();
  },

  // T25: applicant CV upload + matching - same local-only privacy default as thesis analysis above.
  uploadCv: async (file: File): Promise<Applicant> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/v1/applicants`, { method: "POST", body: form });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new ApiError(res.status, detail.detail || `upload -> ${res.status}`);
    }
    return res.json();
  },
  applicant: (id: string) => getJson<Applicant>(`/v1/applicants/${id}`),
  patchApplicantProfile: async (id: string, patch: Record<string, unknown>): Promise<Applicant> => {
    const res = await fetch(`${API_BASE}/v1/applicants/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new ApiError(res.status, detail.detail || `patch -> ${res.status}`);
    }
    return res.json();
  },
  deleteApplicant: async (id: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/v1/applicants/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      throw new ApiError(res.status, `delete -> ${res.status}`);
    }
  },
  // T33 (packet 018, Q4): the matches endpoints are paginated now ({total, results}), never an unbounded
  // bare array - a real applicant's own matches against a 4.7k-row DB must never be dumped in one response.
  computeMatches: async (id: string): Promise<ApplicantMatchesResponse> => {
    const res = await fetch(`${API_BASE}/v1/applicants/${id}/matches`, { method: "POST" });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new ApiError(res.status, detail.detail || `matches -> ${res.status}`);
    }
    return res.json();
  },
  applicantMatches: (id: string) => getJson<ApplicantMatchesResponse>(`/v1/applicants/${id}/matches`),
  matchingWeights: () => getJson<Record<string, number>>("/v1/applicants/matching/weights"),
};
