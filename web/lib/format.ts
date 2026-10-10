import type { Locale } from "./i18n/dictionaries";

const LOCALE_TAG: Record<Locale, string> = { en: "en-GB", "sr-Latn": "sr-Latn-RS", "sr-Cyrl": "sr-Cyrl-RS" };

export function formatMoney(amount: number, locale: Locale): string {
  return new Intl.NumberFormat(LOCALE_TAG[locale], { maximumFractionDigits: 0 }).format(amount);
}

export function formatDate(iso: string, locale: Locale): string {
  try {
    return new Intl.DateTimeFormat(LOCALE_TAG[locale], { year: "numeric", month: "short", day: "numeric" }).format(
      new Date(iso),
    );
  } catch {
    return iso;
  }
}

/** Days until `iso` (negative if past); null if unparsable. Used for the deadline countdown on cards. */
export function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.ceil((t - Date.now()) / 86_400_000);
}

export const FLAG: Record<string, string> = {};
/** ISO 3166-1 alpha-2 -> the flag emoji Intl.DisplayNames can't give us; simple, dependency-free. */
export function countryFlag(iso2: string | null): string {
  // T23 (PM ruling R2): country_iso2 is nullable now (a remote/hybrid/unknown-location listing has no
  // country stated at all, never guessed) - null is a real, expected input here, not an error case.
  if (!iso2 || !/^[A-Z]{2}$/.test(iso2)) return "";
  const codePoints = [...iso2].map((c) => 127397 + c.charCodeAt(0));
  return String.fromCodePoint(...codePoints);
}

export function countryName(iso2: string, locale: Locale): string {
  try {
    return new Intl.DisplayNames([LOCALE_TAG[locale]], { type: "region" }).of(iso2) || iso2;
  } catch {
    return iso2;
  }
}
