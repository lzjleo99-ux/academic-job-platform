import Link from "next/link";
import type { ListingCard as ListingCardT } from "@/lib/types";
import { useI18n } from "@/lib/i18n/context";
import { countryFlag, daysUntil, formatDate, formatMoney } from "@/lib/format";
import { SourceChips } from "./SourceChips";
import { ConfidenceValue } from "./ConfidenceValue";

const KIND_KEY: Record<string, string> = {
  employment: "k_employment", enrollment: "k_enrollment", grant_call: "k_grant_call",
  internship: "k_internship", other: "k_other",
};

export function ListingCard({ listing }: { listing: ListingCardT }) {
  const { t, locale } = useI18n();
  const dLeft = daysUntil(listing.deadline);
  const salary = listing.salary_eur_year;
  const hasSalary = salary.min_eur_year !== null || salary.max_eur_year !== null;

  return (
    <article className="rounded-lg border border-gray-200 p-4 hover:shadow-md transition-shadow bg-white">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-base font-semibold text-gray-900">
          <Link href={`/listing/${listing.id}`} className="hover:underline">
            {listing.title_raw}
          </Link>
        </h3>
        {listing.verified_direct && (
          <span className="shrink-0 text-xs rounded-full bg-green-100 text-green-800 px-2 py-0.5">
            {t("card_verified_direct")}
          </span>
        )}
      </div>
      <div className="mt-1 text-sm text-gray-600">
        {listing.org?.name_en || listing.org_raw || "—"}
        {listing.city && (
          <>
            {" · "}
            {countryFlag(listing.country_iso2)} {listing.city}
          </>
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="text-xs rounded bg-blue-50 text-blue-800 px-2 py-0.5">{t(KIND_KEY[listing.kind] as never)}</span>
        <span className="text-xs rounded bg-purple-50 text-purple-800 px-2 py-0.5">{listing.position_kind}</span>
        <span className="text-xs rounded bg-gray-100 text-gray-700 px-2 py-0.5">
          {t(("v_" + listing.ft_pt) as never)}
        </span>
        {/* T23 (PM ruling R2): country_iso2=null is a real, common state (a genuinely remote/hybrid ad, or one
            whose location just couldn't be resolved) - never left blank with no explanation at all. */}
        {listing.location_class !== "located" && (
          <span
            className="text-xs rounded bg-teal-50 text-teal-800 px-2 py-0.5"
            title={listing.location_class_raw ?? undefined}
          >
            {t(("v_" + listing.location_class) as never)}
          </span>
        )}
      </div>

      <div className="mt-2 text-sm">
        {hasSalary ? (
          <ConfidenceValue
            display={
              <span>
                {salary.min_eur_year && formatMoney(salary.min_eur_year, locale)}
                {salary.min_eur_year && salary.max_eur_year && salary.min_eur_year !== salary.max_eur_year && "–"}
                {salary.max_eur_year && salary.max_eur_year !== salary.min_eur_year && formatMoney(salary.max_eur_year, locale)}
                {" EUR/yr"}
              </span>
            }
            conf={null}
            raw={salary.note}
          />
        ) : (
          <span className="text-gray-400">{t("card_salary_unspecified")}</span>
        )}
      </div>

      <div className="mt-2 text-sm text-gray-600">
        {listing.deadline ? (
          <span className={dLeft !== null && dLeft <= 14 ? "text-red-600 font-medium" : undefined}>
            {t("card_deadline")}: {formatDate(listing.deadline, locale)}
            {dLeft !== null && dLeft >= 0 && ` (${dLeft}d)`}
          </span>
        ) : (
          <span className="text-gray-400">{t("card_no_deadline")}</span>
        )}
      </div>

      <div className="mt-3">
        <SourceChips sources={listing.sources} />
      </div>
    </article>
  );
}
