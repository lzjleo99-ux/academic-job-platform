"use client";
import { useI18n } from "@/lib/i18n/context";
import { LOCALES, LOCALE_LABEL } from "@/lib/i18n/dictionaries";

export function LocaleSwitcher() {
  const { locale, setLocale } = useI18n();
  return (
    <label className="text-sm">
      <span className="sr-only">Language / Jezik / Језик</span>
      <select
        value={locale}
        onChange={(e) => setLocale(e.target.value as never)}
        className="rounded border-gray-300 bg-white text-sm"
      >
        {LOCALES.map((l) => (
          <option key={l} value={l}>
            {LOCALE_LABEL[l]}
          </option>
        ))}
      </select>
    </label>
  );
}
