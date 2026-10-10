"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { GovernanceSnapshot, SourceStatus } from "@/lib/types";

const STATUS_COLOR: Record<string, string> = {
  ok: "bg-green-100 text-green-800",
  degraded: "bg-yellow-100 text-yellow-800",
  never_run: "bg-gray-100 text-gray-700",
  not_applicable: "bg-gray-100 text-gray-500",
};

export default function CompliancePage() {
  const { t } = useI18n();
  const [sources, setSources] = useState<SourceStatus[] | null>(null);
  const [governance, setGovernance] = useState<GovernanceSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.sources(), api.governance()])
      .then(([s, g]) => {
        setSources(s);
        setGovernance(g);
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  // "denied attempts must be 0" (T24, packet 014) - a real, queried total across every source, checked here
  // rather than only asserted in a test, so an operator sees the same real number this platform's own
  // governance guarantee depends on.
  const totalDenied = (sources ?? []).reduce((sum, s) => sum + s.denied_24h, 0);

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <h1 className="text-2xl font-semibold text-gray-900">{t("compliance_title")}</h1>
      <p className="mt-2 text-sm text-gray-600">{t("compliance_intro")}</p>

      {error && <p className="mt-4 text-red-600 text-sm">{t("error_loading")}</p>}
      {!sources && !error && <p className="mt-4 text-sm text-gray-500">{t("loading")}</p>}

      {sources && (
        <div
          data-testid="denied-attempts-total"
          className={`mt-4 inline-block rounded-lg px-3 py-2 text-sm font-medium ${
            totalDenied === 0 ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"
          }`}
        >
          {t("compliance_denied_24h")}: {totalDenied}
        </div>
      )}

      {sources && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm" data-testid="compliance-table">
            <thead>
              <tr className="border-b border-gray-200 text-left text-gray-500">
                <th className="py-2 pr-3">ID</th>
                <th className="py-2 pr-3">{t("compliance_route_type")}</th>
                <th className="py-2 pr-3">{t("compliance_robots_state")}</th>
                <th className="py-2 pr-3">{t("compliance_scheduler_status")}</th>
                <th className="py-2 pr-3">{t("compliance_requests_24h")}</th>
                <th className="py-2 pr-3">{t("compliance_denied_24h")}</th>
                <th className="py-2 pr-3">{t("compliance_last_success")}</th>
                <th className="py-2 pr-3">{t("compliance_blocked_reason")}</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id} className="border-b border-gray-100" data-testid={`source-row-${s.id}`}>
                  <td className="py-2 pr-3 font-mono text-xs">{s.id}</td>
                  <td className="py-2 pr-3">{s.route_type}</td>
                  <td className="py-2 pr-3">
                    {s.robots_ok === null ? "—" : s.robots_ok ? "✓" : "✗"}
                  </td>
                  <td className="py-2 pr-3">
                    <span className={`rounded px-2 py-0.5 text-xs ${STATUS_COLOR[s.last_run_status] ?? "bg-gray-100 text-gray-700"}`}>
                      {s.last_run_status === "never_run" ? t("compliance_never_run") : s.last_run_status}
                    </span>
                  </td>
                  <td className="py-2 pr-3">{s.requests_24h}</td>
                  <td className={`py-2 pr-3 ${s.denied_24h > 0 ? "font-semibold text-red-700" : ""}`}>{s.denied_24h}</td>
                  <td className="py-2 pr-3">{s.last_success_at ? new Date(s.last_success_at).toLocaleString() : "—"}</td>
                  <td className="py-2 pr-3 text-xs text-gray-600">{s.degraded_reasons.join("; ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {governance && governance.per_host_last_24h.length > 0 && (
        <div className="mt-8">
          <h2 className="text-lg font-medium text-gray-900">Discovery activity, last 24h (per host)</h2>
          <table className="mt-2 w-full text-sm" data-testid="discovery-hosts-table">
            <thead>
              <tr className="border-b border-gray-200 text-left text-gray-500">
                <th className="py-2 pr-3">Host</th>
                <th className="py-2 pr-3">Runs</th>
                <th className="py-2 pr-3">Paths fetched</th>
                <th className="py-2 pr-3">Denied</th>
              </tr>
            </thead>
            <tbody>
              {governance.per_host_last_24h.map((h) => (
                <tr key={h.host} className="border-b border-gray-100">
                  <td className="py-2 pr-3 font-mono text-xs">{h.host}</td>
                  <td className="py-2 pr-3">{h.runs_24h}</td>
                  <td className="py-2 pr-3">{h.paths_fetched_24h}</td>
                  <td className={`py-2 pr-3 ${h.denied_24h > 0 ? "font-semibold text-red-700" : ""}`}>{h.denied_24h}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
