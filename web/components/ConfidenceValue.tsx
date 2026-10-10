"use client";
// D6: every uncertain field keeps {v, conf, raw}. This is the one place that shows a value AND lets the
// viewer see exactly what the ad said and how confident the extractor was - on click (keyboard: Enter/Space,
// same handler as click on a <button>) or hover (title attribute, works without JS/mouse-only too).
import { useState } from "react";
import { useI18n } from "@/lib/i18n/context";

function confidenceColor(conf: number | null): string {
  if (conf === null) return "bg-gray-300";
  if (conf >= 0.8) return "bg-green-500";
  if (conf >= 0.5) return "bg-yellow-500";
  return "bg-orange-500";
}

export function ConfidenceValue({
  display,
  conf,
  raw,
}: {
  display: React.ReactNode;
  conf: number | null;
  raw: string | null;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  if (raw === null && conf === null) return <>{display}</>;
  return (
    <span className="relative inline-flex items-center gap-1">
      <button
        type="button"
        className="inline-flex items-center gap-1 rounded hover:bg-gray-100 px-1 -mx-1"
        aria-expanded={open}
        aria-label={`${t("confidence")}: ${conf ?? "?"}`}
        title={raw ? `${t("raw_excerpt")}: "${raw}"` : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        {display}
        <span
          className={`inline-block h-2 w-2 rounded-full ${confidenceColor(conf)}`}
          data-testid="confidence-dot"
        />
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute z-10 top-full left-0 mt-1 w-64 rounded border border-gray-300 bg-white p-2 text-xs text-gray-700 shadow-lg"
        >
          {conf !== null && (
            <div>
              {t("confidence")}: {(conf * 100).toFixed(0)}%
            </div>
          )}
          {raw && (
            <div className="mt-1 italic">
              {t("raw_excerpt")}: &ldquo;{raw}&rdquo;
            </div>
          )}
        </span>
      )}
    </span>
  );
}
