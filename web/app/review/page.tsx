"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { ClassificationReview, ClassificationReviewReason } from "@/lib/types";

// T48: a human-readable view of the classifier's own review queue. The API returns JSON, which is
// machine-shaped and unreadable to a person; this page renders the same rows the way a reviewer needs
// them - the verdict, the org, the ad, and above all THE EVIDENCE the decision was made on, because a
// review decision without the excerpt it is judging is a guess.
//
// Deliberately READ-ONLY: recording a decision requires AJP_ADMIN_TOKEN (ajp.api.deps.require_admin), and
// a browser cannot hold a secret - shipping a decision button here would either be a lie or would leak
// the token into the page bundle. The operator acts on the queue with a token; this page is for reading
// the worklist, and doubles as the public transparency statement about what the classifier is unsure of.
const REASONS: ClassificationReviewReason[] = ["unclassified_academic", "excluded_by_rule", "low_confidence"];

export default function ReviewPage() {
  const { t } = useI18n();
  const [data, setData] = useState<Awaited<ReturnType<typeof api.classificationReview>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState<ClassificationReviewReason | null>(null);

  useEffect(() => {
    setData(null);
    api
      .classificationReview(reason ?? undefined)
      .then(setData)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [reason]);

  const items: ClassificationReview[] = data?.items ?? [];
  const counts = data?.pending_by_reason ?? {};

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <h1 className="text-2xl font-semibold text-gray-900">{t("review_title")}</h1>
      <p className="mt-2 text-sm text-gray-600">{t("review_intro")}</p>

      <dl className="mt-4 flex flex-wrap gap-6 text-sm">
        <div>
          <dt className="text-gray-500">{t("review_total_pending")}</dt>
          <dd className="text-lg font-semibold text-gray-900">{data?.total ?? "…"}</dd>
        </div>
        {data && (
          <div>
            <dt className="text-gray-500">{t("review_model")}</dt>
            <dd className="font-mono text-xs text-gray-700">{data.model}</dd>
          </div>
        )}
      </dl>

      {/* reason filter: the queue is ordered worst-first, so an unfiltered view already leads with the
          real misses; the filter exists so a reviewer can work one bucket to the end. */}
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setReason(null)}
          className={`rounded px-3 py-1.5 text-sm ${reason === null ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"}`}
        >
          {t("review_filter_all")}
        </button>
        {REASONS.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => setReason(r)}
            className={`rounded px-3 py-1.5 text-sm ${reason === r ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"}`}
          >
            {t(`review_reason_${r}` as "review_title")} ({counts[r] ?? 0})
          </button>
        ))}
      </div>

      {error && <p className="mt-4 text-red-600 text-sm">{t("error_loading")}</p>}
      {!data && !error && <p className="mt-4 text-sm text-gray-500">{t("loading")}</p>}

      <ul className="mt-4 space-y-3" data-testid="review-list">
        {items.map((r) => (
          <li key={r.id} className="rounded-lg border border-gray-200 p-4" data-testid="review-item">
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${reasonClass(r.reason)}`}>
                  {t(`review_reason_${r.reason}` as "review_title")}
                </span>
                <h2 className="mt-1 font-medium text-gray-900">{r.listing_title ?? "—"}</h2>
                {r.listing_org && <p className="text-sm text-gray-600">{r.listing_org}</p>}
              </div>
              {r.listing_url && (
                <a
                  href={r.listing_url}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  className="shrink-0 rounded bg-gray-100 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-200"
                >
                  {t("review_open_ad")}
                </a>
              )}
            </div>

            {/* the evidence block - the whole point of the queue */}
            <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
              <Row label={t("review_machine_verdict")} value={r.classified_as ?? "—"} />
              <Row
                label={t("confidence")}
                value={r.conf === null ? t("value_unknown") : `${r.conf} ${t("review_of_threshold")} ${data?.threshold ?? 0.7}`}
              />
              <Row label={t("review_rule")} value={r.rule ?? "—"} mono />
              <Row label={t("review_zone")} value={r.zone ?? "—"} />
              <Row
                label={t("raw_excerpt")}
                value={r.raw ? `"${r.raw}"` : t("review_no_excerpt")}
                mono
                wide
              />
              {r.guards.length > 0 && (
                <Row label={t("review_guards")} value={r.guards.join(", ")} wide />
              )}
            </dl>
            <p className="mt-2 text-xs text-gray-400">
              {t("review_model")}: {r.model} · {t("review_decisions_note")}
            </p>
          </li>
        ))}
      </ul>
      {data && items.length === 0 && <p className="mt-4 text-sm text-gray-500">{t("no_results")}</p>}
    </div>
  );
}

function Row({ label, value, mono, wide }: { label: string; value: string; mono?: boolean; wide?: boolean }) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <dt className="inline text-gray-500">{label}: </dt>
      <dd className={`inline ${mono ? "font-mono" : ""} text-gray-800`}>{value}</dd>
    </div>
  );
}

function reasonClass(reason: ClassificationReviewReason): string {
  // worst first in colour too: a real ad we failed to classify is the T33 failure mode and the only
  // bucket that can hide a genuine opening; a broad rule is merely imprecise.
  switch (reason) {
    case "unclassified_academic":
      return "bg-amber-100 text-amber-800";
    case "excluded_by_rule":
      return "bg-purple-100 text-purple-800";
    default:
      return "bg-gray-100 text-gray-700";
  }
}
