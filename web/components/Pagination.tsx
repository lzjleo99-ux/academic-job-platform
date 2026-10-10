import { useI18n } from "@/lib/i18n/context";
import type { Page } from "@/lib/types";

export function Pagination({ page, onChange }: { page: Page; onChange: (p: number) => void }) {
  const { t } = useI18n();
  if (page.total_pages <= 1) return null;
  return (
    <nav className="mt-4 flex items-center justify-between" aria-label="pagination">
      <button
        type="button"
        className="rounded border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-40"
        disabled={page.page <= 1}
        onClick={() => onChange(page.page - 1)}
      >
        {t("prev")}
      </button>
      <span className="text-sm text-gray-600">{t("page_of", { page: page.page, total: page.total_pages })}</span>
      <button
        type="button"
        className="rounded border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-40"
        disabled={page.page >= page.total_pages}
        onClick={() => onChange(page.page + 1)}
      >
        {t("next")}
      </button>
    </nav>
  );
}
