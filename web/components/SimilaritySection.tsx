"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { SimilarityReport, SpanMatch } from "@/lib/types";

const BAND_COLOR: Record<string, string> = {
  very_low: "text-gray-500", low: "text-yellow-700", medium: "text-orange-700", high: "text-green-700",
};

function SpanRow({ m }: { m: SpanMatch }) {
  return (
    <li className={`rounded border p-2 text-sm ${m.signal === "verbatim" ? "border-red-200 bg-red-50" : "border-yellow-200 bg-yellow-50"}`}>
      <div className="italic">&ldquo;{m.snippet}&rdquo;</div>
      <div className="mt-1 text-xs text-gray-600 flex flex-wrap gap-2 items-center">
        <span className="font-mono uppercase">{m.signal}</span>
        <span>score {m.score.toFixed(2)}</span>
        <span>source: {m.source_id}</span>
        {m.title && <span>&mdash; {m.title}</span>}
        {m.source_url && (
          <a href={m.source_url} target="_blank" rel="noopener noreferrer nofollow" className="text-blue-600 hover:underline">
            link
          </a>
        )}
      </div>
    </li>
  );
}

export function SimilaritySection({ taskId }: { taskId: string }) {
  const { t } = useI18n();
  const [sourcesAvailable, setSourcesAvailable] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [copyleaksConsent, setCopyleaksConsent] = useState(false);
  const [report, setReport] = useState<SimilarityReport | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadedExisting, setLoadedExisting] = useState(false);

  useEffect(() => {
    api.similaritySources().then((s) => {
      setSourcesAvailable(s);
      setSelected(new Set(s.filter((x) => x !== "copyleaks")));  // copyleaks opt-in only, never pre-selected
    }).catch(() => void 0);
    api.getSimilarity(taskId).then(setReport).catch(() => void 0).finally(() => setLoadedExisting(true));
  }, [taskId]);

  function toggle(source: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(source)) next.delete(source);
      else next.add(source);
      return next;
    });
  }

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const r = await api.runSimilarity(taskId, [...selected], copyleaksConsent && selected.has("copyleaks"));
      setReport(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="mt-8 border-t pt-6">
      <h2 className="text-lg font-semibold text-gray-900">{t("similarity_title")}</h2>

      <fieldset className="mt-3">
        <legend className="text-sm font-medium text-gray-800 mb-1">{t("similarity_sources")}</legend>
        <div className="flex flex-wrap gap-3">
          {sourcesAvailable.map((s) => (
            <label key={s} className="flex items-center gap-1 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" checked={selected.has(s)} onChange={() => toggle(s)} className="rounded border-gray-300" />
              {s}
            </label>
          ))}
        </div>
        {selected.has("copyleaks") && (
          <label className="mt-2 flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input type="checkbox" checked={copyleaksConsent} onChange={(e) => setCopyleaksConsent(e.target.checked)}
                  className="rounded border-gray-300" />
            {t("similarity_copyleaks_consent_label")}
          </label>
        )}
      </fieldset>

      <button type="button" onClick={run} disabled={running}
             className="mt-3 rounded bg-blue-600 text-white px-4 py-2 text-sm hover:bg-blue-700 disabled:opacity-50">
        {running ? t("similarity_running") : t("similarity_run_button")}
      </button>

      {error && <p className="mt-2 text-red-600 text-sm">{error}</p>}

      {!report && loadedExisting && !running && (
        <p className="mt-3 text-sm text-gray-500">{t("similarity_no_run_yet")}</p>
      )}

      {report && (
        <div className="mt-4" data-testid="similarity-report">
          <div className="flex flex-wrap gap-6 text-sm">
            <div>
              <span className="text-gray-500">{t("similarity_estimate")}: </span>
              <span className="font-semibold">{report.estimate_pct}%</span>
            </div>
            <div>
              <span className="text-gray-500">{t("similarity_confidence_band")}: </span>
              <span className={`font-semibold ${BAND_COLOR[report.confidence_band]}`}>{report.confidence_band}</span>
            </div>
          </div>
          <p className="mt-1 text-xs text-gray-600">{report.coverage_statement}</p>

          <div className="mt-3 space-y-1">
            {report.disclaimers.map((d, i) => (
              <p key={i} className="rounded border border-blue-200 bg-blue-50 p-2 text-xs text-blue-900">{d}</p>
            ))}
          </div>

          {report.copyleaks && (
            <div className="mt-3 rounded border border-purple-200 bg-purple-50 p-2 text-xs text-purple-900">
              Copyleaks: {JSON.stringify(report.copyleaks)}
            </div>
          )}

          <div className="mt-4">
            <h3 className="text-sm font-semibold text-gray-800">{t("similarity_verbatim_matches")}</h3>
            {report.matched_spans.length === 0 ? (
              <p className="text-sm text-gray-500">{t("similarity_no_matches")}</p>
            ) : (
              <ul className="mt-1 space-y-1" data-testid="verbatim-spans">
                {report.matched_spans.map((m, i) => <SpanRow key={i} m={m} />)}
              </ul>
            )}
          </div>

          <div className="mt-4">
            <h3 className="text-sm font-semibold text-gray-800">{t("similarity_paraphrase_matches")}</h3>
            {report.paraphrase_spans.length === 0 ? (
              <p className="text-sm text-gray-500">{t("similarity_no_matches")}</p>
            ) : (
              <ul className="mt-1 space-y-1" data-testid="paraphrase-spans">
                {report.paraphrase_spans.map((m, i) => <SpanRow key={i} m={m} />)}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
