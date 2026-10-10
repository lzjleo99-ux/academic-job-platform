"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";

export default function ThesisUploadPage() {
  const { t } = useI18n();
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [templates, setTemplates] = useState<string[]>([]);
  const [template, setTemplate] = useState("rs_phd_generic");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.thesisTemplates().then((ts) => {
      setTemplates(ts);
      if (ts.length && !ts.includes(template)) setTemplate(ts[0]);
    }).catch(() => void 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setError(t("thesis_no_file"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const task = await api.analyzeThesis(file, template);
      router.push(`/thesis/${task.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-6">
      <h1 className="text-2xl font-semibold text-gray-900">{t("thesis_title")}</h1>
      <p className="mt-2 rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900" role="note">
        {t("thesis_local_only_note")}
      </p>

      <form onSubmit={onSubmit} className="mt-6 space-y-4">
        <div>
          <label htmlFor="thesis-file" className="block text-sm font-medium text-gray-800">
            {t("thesis_upload_label")}
          </label>
          <input id="thesis-file" ref={fileRef} type="file" accept=".docx,.pdf"
                className="mt-1 block w-full text-sm" />
        </div>
        <div>
          <label htmlFor="thesis-template" className="block text-sm font-medium text-gray-800">
            {t("thesis_template_label")}
          </label>
          <select id="thesis-template" value={template} onChange={(e) => setTemplate(e.target.value)}
                 className="mt-1 rounded border-gray-300 text-sm">
            {templates.map((tpl) => (
              <option key={tpl} value={tpl}>{tpl}</option>
            ))}
          </select>
        </div>
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <button type="submit" disabled={busy}
               className="rounded bg-blue-600 text-white px-4 py-2 text-sm hover:bg-blue-700 disabled:opacity-50">
          {busy ? t("thesis_analyzing") : t("thesis_analyze_button")}
        </button>
      </form>
    </div>
  );
}
