"use client";
import { cloneElement, useRef, useState } from "react";
import type { ReactElement } from "react";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { Applicant, ApplicantMatch, ApplicantProfile } from "@/lib/types";

type Step = "upload" | "review" | "matches";

// Array-shaped profile fields are edited as a single comma-separated text field (simplest honest editor for
// a heuristically-extracted list - an applicant correcting one wrong entry just retypes the whole short list,
// never a multi-field form for an open-ended count of items).
function joinList(v?: string[] | null): string {
  return (v || []).join(", ");
}
function splitList(v: string): string[] {
  return v.split(",").map((s) => s.trim()).filter(Boolean);
}

export default function ApplicantsPage() {
  const { t } = useI18n();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [step, setStep] = useState<Step>("upload");
  const [applicant, setApplicant] = useState<Applicant | null>(null);
  const [matches, setMatches] = useState<ApplicantMatch[] | null>(null);
  const [matchesTotal, setMatchesTotal] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletedNote, setDeletedNote] = useState(false);

  // Local editable copy of the profile - only touched fields are sent on save (a PATCH merge, never a
  // full replace), but the form itself needs a full local draft to edit against.
  const [draft, setDraft] = useState<ApplicantProfile | null>(null);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setError(t("applicants_no_file"));
      return;
    }
    setBusy(true);
    setError(null);
    setDeletedNote(false);
    try {
      const a = await api.uploadCv(file);
      setApplicant(a);
      setDraft(a.profile);
      if (a.status === "failed") {
        setError(a.error || t("applicants_parse_failed"));
      } else {
        setStep("review");
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSaveProfile() {
    if (!applicant || !draft) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.patchApplicantProfile(applicant.id, draft as Record<string, unknown>);
      setApplicant(updated);
      setDraft(updated.profile);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onFindMatches() {
    if (!applicant) return;
    setBusy(true);
    setError(null);
    try {
      const page = await api.computeMatches(applicant.id);
      setMatches(page.results);
      setMatchesTotal(page.total);
      setStep("matches");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (!applicant) return;
    if (!window.confirm(t("applicants_delete_confirm"))) return;
    setBusy(true);
    try {
      await api.deleteApplicant(applicant.id);
    } finally {
      setBusy(false);
      setApplicant(null);
      setDraft(null);
      setMatches(null);
      setStep("upload");
      setDeletedNote(true);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <h1 className="text-2xl font-semibold text-gray-900">{t("applicants_title")}</h1>
      <p className="mt-2 rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900" role="note">
        {t("applicants_local_only_note")}
      </p>
      {deletedNote && (
        <p className="mt-2 rounded border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700" data-testid="applicants-deleted-note">
          {t("applicants_deleted_note")}
        </p>
      )}

      {step === "upload" && (
        <form onSubmit={onUpload} className="mt-6 space-y-4">
          <div>
            <label htmlFor="cv-file" className="block text-sm font-medium text-gray-800">
              {t("applicants_upload_label")}
            </label>
            <input id="cv-file" ref={fileRef} type="file" accept=".docx,.pdf" className="mt-1 block w-full text-sm" />
          </div>
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <button type="submit" disabled={busy}
                 className="rounded bg-blue-600 text-white px-4 py-2 text-sm hover:bg-blue-700 disabled:opacity-50">
            {busy ? t("applicants_uploading") : t("applicants_upload_button")}
          </button>
        </form>
      )}

      {step === "review" && applicant && draft && (
        <div className="mt-6 space-y-4" data-testid="applicants-review">
          <h2 className="text-lg font-medium text-gray-900">{t("applicants_review_title")}</h2>
          <p className="text-sm text-gray-600">{t("applicants_edit_hint")}</p>

          <ProfileField label={t("applicants_f_disciplines")}>
            <input className="mt-1 block w-full rounded border-gray-300 text-sm" value={joinList(draft.disciplines)}
                  onChange={(e) => setDraft({ ...draft, disciplines: splitList(e.target.value) })} />
          </ProfileField>
          <ProfileField label={t("applicants_f_degree")}>
            <select className="mt-1 block w-full rounded border-gray-300 text-sm"
                   value={draft.degree?.v || ""}
                   onChange={(e) => setDraft({ ...draft, degree: e.target.value ? { v: e.target.value, raw: e.target.value } : null })}>
              <option value="">{t("value_unknown")}</option>
              {["bsc", "msc", "phd", "postdoc", "prof"].map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </ProfileField>
          <ProfileField label={t("applicants_f_skills")}>
            <input className="mt-1 block w-full rounded border-gray-300 text-sm" value={joinList(draft.skills)}
                  onChange={(e) => setDraft({ ...draft, skills: splitList(e.target.value) })} />
          </ProfileField>
          <ProfileField label={t("applicants_f_languages")}>
            <input className="mt-1 block w-full rounded border-gray-300 text-sm"
                  value={(draft.languages || []).map((l) => l.lang).join(", ")}
                  onChange={(e) => setDraft({ ...draft, languages: splitList(e.target.value).map((lang) => ({ lang, level: null })) })} />
          </ProfileField>
          <ProfileField label={t("applicants_f_desired_countries")}>
            <input className="mt-1 block w-full rounded border-gray-300 text-sm" value={joinList(draft.desired_countries)}
                  onChange={(e) => setDraft({ ...draft, desired_countries: splitList(e.target.value) })} />
          </ProfileField>
          <ProfileField label={t("applicants_f_desired_cities")}>
            <input className="mt-1 block w-full rounded border-gray-300 text-sm" value={joinList(draft.desired_cities)}
                  onChange={(e) => setDraft({ ...draft, desired_cities: splitList(e.target.value) })} />
          </ProfileField>
          <label className="flex items-center gap-2 text-sm text-gray-800">
            <input type="checkbox" checked={!!draft.remote_ok}
                  onChange={(e) => setDraft({ ...draft, remote_ok: e.target.checked })} />
            {t("applicants_f_remote_ok")}
          </label>
          <ProfileField label={t("applicants_f_ft_pt")}>
            <select className="mt-1 block w-full rounded border-gray-300 text-sm" value={draft.ft_pt || ""}
                   onChange={(e) => setDraft({ ...draft, ft_pt: (e.target.value || null) as "full" | "part" | null })}>
              <option value="">{t("value_unknown")}</option>
              <option value="full">{t("v_full")}</option>
              <option value="part">{t("v_part")}</option>
            </select>
          </ProfileField>
          <ProfileField label={t("applicants_f_years_experience")}>
            <input type="number" min={0} className="mt-1 block w-32 rounded border-gray-300 text-sm"
                  value={draft.years_experience ?? ""}
                  onChange={(e) => setDraft({ ...draft, years_experience: e.target.value ? Number(e.target.value) : null })} />
          </ProfileField>
          <ProfileField label={t("applicants_f_min_salary")}>
            <input type="number" min={0} className="mt-1 block w-40 rounded border-gray-300 text-sm"
                  value={draft.min_salary_eur_year ?? ""}
                  onChange={(e) => setDraft({ ...draft, min_salary_eur_year: e.target.value ? Number(e.target.value) : null })} />
          </ProfileField>
          <ProfileField label={t("applicants_f_visa_note")}>
            <input className="mt-1 block w-full rounded border-gray-300 text-sm" value={draft.foreigner_visa_note || ""}
                  onChange={(e) => setDraft({ ...draft, foreigner_visa_note: e.target.value || null })} />
          </ProfileField>

          {error && <p className="text-red-600 text-sm">{error}</p>}
          <div className="flex items-center gap-3">
            <button type="button" onClick={onSaveProfile} disabled={busy}
                   className="rounded border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50 disabled:opacity-50">
              {busy ? t("applicants_saving") : t("applicants_save_button")}
            </button>
            <button type="button" onClick={onFindMatches} disabled={busy}
                   className="rounded bg-blue-600 text-white px-4 py-2 text-sm hover:bg-blue-700 disabled:opacity-50">
              {busy ? t("applicants_computing") : t("applicants_find_matches_button")}
            </button>
            <button type="button" onClick={onDelete} disabled={busy}
                   className="ml-auto rounded border border-red-300 px-4 py-2 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50">
              {t("applicants_delete_button")}
            </button>
          </div>
        </div>
      )}

      {step === "matches" && (
        <div className="mt-6 space-y-4" data-testid="applicants-matches">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-medium text-gray-900">{t("applicants_matches_title")}</h2>
            <button type="button" onClick={() => setStep("review")} className="text-sm text-blue-600 hover:underline">
              {t("applicants_review_title")}
            </button>
          </div>
          {(!matches || matches.length === 0) && (
            <p className="text-sm text-gray-600" data-testid="applicants-no-matches">{t("applicants_no_matches")}</p>
          )}
          {/* T33 (packet 018, Q4): the endpoint is paginated (top-100 by score) now - shown honestly rather
              than silently implying this IS every open listing when there may be many more, lower-ranked ones. */}
          {matches && matches.length > 0 && matchesTotal !== null && matchesTotal > matches.length && (
            <p className="text-xs text-gray-500" data-testid="applicants-matches-total-note">
              {t("applicants_showing_top_of_total").replace("{shown}", String(matches.length)).replace("{total}", String(matchesTotal))}
            </p>
          )}
          <ul className="space-y-3" data-testid="applicants-match-list">
            {(matches || []).map((m) => (
              <MatchCard key={m.listing_id} match={m} t={t} />
            ))}
          </ul>
          <button type="button" onClick={onDelete} disabled={busy}
                 className="rounded border border-red-300 px-4 py-2 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50">
            {t("applicants_delete_button")}
          </button>
        </div>
      )}
    </div>
  );
}

// Found live building this page's own E2E test: a <label> merely placed NEXT TO its input, with no
// htmlFor/id association, is not a real accessible label at all - a screen reader (and Playwright's own
// getByLabel, which uses the identical accessibility-tree lookup) cannot associate the two. Every field now
// gets a real, stable id derived from its own label text, wired to both the <label htmlFor> and the
// input/select via cloneElement, rather than trusting visual proximity alone.
function ProfileField({ label, children }: { label: string; children: ReactElement }) {
  const id = "applicant-field-" + label.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-gray-800">{label}</label>
      {cloneElement(children, { id } as any)}
    </div>
  );
}

function MatchCard({ match, t }: { match: ApplicantMatch; t: (k: any) => string }) {
  return (
    <li className="rounded border border-gray-200 p-4" data-testid="applicant-match-card">
      <div className="flex items-baseline justify-between gap-4">
        <a href={`/listing/${match.listing_id}`} className="font-medium text-blue-700 hover:underline">
          {match.title_raw || t("value_unknown")}
        </a>
        <span className="text-sm font-semibold text-gray-900" title={`${t("applicants_score_label")}: ${match.score}`}>
          {Math.round(match.score)}/100
        </span>
      </div>
      <p className="text-sm text-gray-600">
        {match.org_raw} {match.city ? `· ${match.city}` : ""} {match.country_iso2 ? `(${match.country_iso2})` : ""}
        {match.location_class === "remote" ? ` · ${t("v_remote")}` : ""}
      </p>
      {/* T33 (packet 018, Q4): coverage - the share of the score backed by REAL positive evidence, never just
          the bare number - a sparse ~50 (mostly 'unknown') must never look identical to a well-evidenced one. */}
      <p className="text-xs text-gray-500" data-testid="applicant-match-coverage">
        {t("applicants_coverage_label")}: {Math.round(match.coverage * 100)}%
        {" · "}
        {match.counts.positive} {t("applicants_matched_label")}, {match.counts.negative} {t("applicants_mismatched_label")},{" "}
        {match.counts.unknown} {t("applicants_unknown_label")}
      </p>
      <details className="mt-2 text-sm text-gray-700">
        <summary className="cursor-pointer text-gray-600">{t("applicants_why_label")}</summary>
        <ul className="mt-1 list-disc pl-5">
          {Object.entries(match.factors).map(([name, f]) => (
            <li key={name} data-testid={`applicant-match-factor-${name}`}>
              <span className={
                f.state === "positive" ? "text-green-700" : f.state === "negative" ? "text-red-700" : "text-gray-500"
              }>
                {name} ({f.state})
              </span>
              : {f.reason} ({f.points}/{f.weight})
            </li>
          ))}
        </ul>
      </details>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
        {match.apply_url && (
          <a href={match.apply_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
            {t("card_apply")}
          </a>
        )}
        {(match.contact_emails || []).map((e) => (
          <a key={e} href={`mailto:${e}`} className="text-blue-600 hover:underline">
            {e}
          </a>
        ))}
        {(match.contact_phones || []).map((p) => (
          <a key={p} href={`tel:${p.replace(/[^+\d]/g, "")}`} className="text-blue-600 hover:underline">
            {p}
          </a>
        ))}
      </div>
    </li>
  );
}
