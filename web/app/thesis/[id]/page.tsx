"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { ThesisFinding, ThesisTask } from "@/lib/types";
import { SimilaritySection } from "@/components/SimilaritySection";

const SEVERITY_STYLE: Record<string, string> = {
  pass: "border-green-200 bg-green-50 text-green-800",
  warn: "border-yellow-200 bg-yellow-50 text-yellow-800",
  fail: "border-red-200 bg-red-50 text-red-800",
};

function FindingRow({ f }: { f: ThesisFinding }) {
  return (
    <li className={`rounded border p-2 text-sm ${SEVERITY_STYLE[f.severity] || ""}`}>
      <span className="font-mono text-xs uppercase">{f.severity}</span>{" "}
      <span className="font-medium">{f.check}</span>: {f.message}
      {f.page != null && <span className="text-xs opacity-70"> (p.{f.page})</span>}
      {f.excerpt && <div className="mt-1 italic text-xs opacity-80">&ldquo;{f.excerpt}&rdquo;</div>}
    </li>
  );
}

export default function ThesisReportPage() {
  const { id } = useParams<{ id: string }>();
  const { t } = useI18n();
  const [task, setTask] = useState<ThesisTask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleted, setDeleted] = useState(false);

  async function onDelete() {
    if (!id || !window.confirm(t("thesis_delete_confirm"))) return;
    await api.deleteThesis(id);
    setDeleted(true);
  }

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const t = await api.thesisTask(id);
        if (cancelled) return;
        setTask(t);
        if (t.status === "processing") timer = setTimeout(poll, 1500);
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      }
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id]);

  if (deleted) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-6">
        <p className="text-sm text-gray-700" data-testid="thesis-deleted-note">{t("thesis_deleted_note")}</p>
        <Link href="/thesis" className="mt-2 inline-block text-sm text-blue-600 hover:underline">
          {"←"} {t("thesis_title")}
        </Link>
      </div>
    );
  }
  if (error) return <div className="mx-auto max-w-3xl px-4 py-6 text-red-600 text-sm">{t("error_loading")}</div>;
  if (!task) return <div className="mx-auto max-w-3xl px-4 py-6 text-sm text-gray-500">{t("loading")}</div>;

  const byCheck = new Map<string, ThesisFinding[]>();
  for (const f of task.report?.findings ?? []) {
    byCheck.set(f.check, [...(byCheck.get(f.check) ?? []), f]);
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <Link href="/thesis" className="text-sm text-blue-600 hover:underline">
        {"←"} {t("thesis_title")}
      </Link>
      <h1 className="mt-2 text-2xl font-semibold text-gray-900">{task.original_filename}</h1>
      <p className="text-sm text-gray-600">Template: {task.template_name}</p>

      {task.status === "processing" && <p className="mt-4 text-sm text-gray-500">{t("thesis_analyzing")}</p>}
      {task.status === "failed" && <p className="mt-4 text-sm text-red-600">{task.error}</p>}

      {task.report && (
        <>
          <div className="mt-4 flex gap-4 text-sm" data-testid="thesis-summary">
            <span className="text-green-700 font-medium">{task.report.summary.pass} {t("thesis_pass")}</span>
            <span className="text-yellow-700 font-medium">{task.report.summary.warn} {t("thesis_warn")}</span>
            <span className="text-red-700 font-medium">{task.report.summary.fail} {t("thesis_fail")}</span>
          </div>

          <div className="mt-4 flex gap-3">
            <a href={api.thesisReportPdfUrl(task.id)} className="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50">
              {t("thesis_download_pdf")}
            </a>
            <a href={api.thesisExportDocxUrl(task.id)} className="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50">
              {t("thesis_download_docx")}
            </a>
            <button type="button" onClick={onDelete}
                   className="ml-auto rounded border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50">
              {t("thesis_delete_button")}
            </button>
          </div>

          <div className="mt-6 space-y-4">
            {[...byCheck.entries()].map(([check, findings]) => (
              <section key={check}>
                <h2 className="text-sm font-semibold text-gray-800 mb-1">{check}</h2>
                <ul className="space-y-1">
                  {findings.map((f, i) => <FindingRow key={i} f={f} />)}
                </ul>
              </section>
            ))}
          </div>
        </>
      )}

      {task.status === "done" && <SimilaritySection taskId={task.id} />}
    </div>
  );
}
