import type { SourceRef } from "@/lib/types";
import { useI18n } from "@/lib/i18n/context";

export function SourceChips({ sources }: { sources: SourceRef[] }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-wrap gap-1" data-testid="source-chips">
      {sources.map((s) => (
        <a
          key={s.source_id}
          href={s.url}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className={`text-xs rounded-full px-2 py-0.5 border ${
            s.verified_direct
              ? "border-green-600 bg-green-50 text-green-800"
              : "border-gray-300 bg-gray-50 text-gray-700"
          }`}
          title={s.verified_direct ? t("card_verified_direct") : s.source_id}
        >
          {s.verified_direct ? "✓ " : ""}
          {s.source_id}
        </a>
      ))}
    </div>
  );
}
