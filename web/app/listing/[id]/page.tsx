"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import { formatDate, formatMoney } from "@/lib/format";
import type { ListingDetail } from "@/lib/types";
import { ConfidenceValue } from "@/components/ConfidenceValue";
import { SourceChips } from "@/components/SourceChips";
import { MapViewClient } from "@/components/MapViewClient";

export default function ListingDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useI18n();
  const [listing, setListing] = useState<ListingDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api
      .listing(id)
      .then(setListing)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [id]);

  if (error) return <div className="mx-auto max-w-3xl px-4 py-6 text-red-600 text-sm">{t("error_loading")}</div>;
  if (!listing) return <div className="mx-auto max-w-3xl px-4 py-6 text-sm text-gray-500">{t("loading")}</div>;

  const salary = listing.salary_eur_year;

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <Link href="/" className="text-sm text-blue-600 hover:underline">
        {"←"} {t("detail_back")}
      </Link>

      <h1 className="mt-2 text-2xl font-semibold text-gray-900">{listing.title_raw}</h1>
      <p className="text-gray-600 mt-1">
        {listing.org?.name_en || listing.org_raw}
        {listing.city && ` · ${listing.city}`}
        {listing.country_iso2 && `, ${listing.country_iso2}`}
        {/* T23 (PM ruling R2): country_iso2=null is a real, common state - shown as a real label, never a
            blank trailing comma with nothing after it. */}
        {listing.location_class !== "located" && (
          <span className="ml-1" data-testid="location-class-badge" title={listing.location_class_raw ?? undefined}>
            {listing.city || listing.country_iso2 ? " · " : ""}
            {t(("v_" + listing.location_class) as never)}
          </span>
        )}
      </p>

      {listing.verified_direct && (
        <span className="mt-2 inline-block text-xs rounded-full bg-green-100 text-green-800 px-2 py-0.5">
          {t("card_verified_direct")}
        </span>
      )}

      <dl className="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
        <div>
          <dt className="text-gray-500">{t("f_kind")}</dt>
          <dd>{listing.kind}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{t("f_position_kind")}</dt>
          <dd>{listing.position_kind}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{t("f_ft_pt")}</dt>
          <dd>{t(("v_" + listing.ft_pt) as never)}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{t("f_contract")}</dt>
          <dd>{listing.contract ?? t("not_stated")}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{t("f_duration")}</dt>
          <dd>
            <ConfidenceValue
              display={listing.duration_months.v ? `${listing.duration_months.v} mo` : t("value_unknown")}
              conf={listing.duration_months.conf}
              raw={listing.duration_months.raw}
            />
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">{t("f_study_while_working")}</dt>
          <dd>
            <ConfidenceValue
              display={
                listing.study_while_work_ok.v === null
                  ? t("value_unknown")
                  : listing.study_while_work_ok.v
                    ? t("v_yes")
                    : t("v_no")
              }
              conf={listing.study_while_work_ok.conf}
              raw={listing.study_while_work_ok.raw}
            />
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">{t("f_degree")}</dt>
          <dd>{listing.degree_required ?? t("value_unknown")}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{t("f_foreigner")}</dt>
          <dd>{t(("v_" + listing.foreigner_eligible) as never)}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{t("card_deadline")}</dt>
          <dd>{listing.deadline ? formatDate(listing.deadline, locale) : t("card_no_deadline")}</dd>
        </div>
        <div>
          <dt className="text-gray-500">{t("f_salary")}</dt>
          <dd>
            {salary.min_eur_year || salary.max_eur_year ? (
              <ConfidenceValue
                display={
                  <>
                    {salary.min_eur_year && formatMoney(salary.min_eur_year, locale)}
                    {salary.min_eur_year && salary.max_eur_year && salary.min_eur_year !== salary.max_eur_year && "–"}
                    {salary.max_eur_year && salary.max_eur_year !== salary.min_eur_year && formatMoney(salary.max_eur_year, locale)}
                    {" EUR/yr"}
                  </>
                }
                conf={null}
                raw={listing.salary_raw ?? salary.note}
              />
            ) : (
              t("card_salary_unspecified")
            )}
          </dd>
        </div>
      </dl>

      {listing.disciplines_euroscivoc.length > 0 && (
        <div className="mt-4">
          <h2 className="text-sm font-medium text-gray-800">{t("detail_disciplines")}</h2>
          <div className="mt-1 flex flex-wrap gap-1">
            {listing.disciplines_euroscivoc.map((d) => (
              <span key={d} className="text-xs rounded bg-gray-100 px-2 py-0.5">
                {d}
              </span>
            ))}
          </div>
        </div>
      )}

      {listing.language.length > 0 && (
        <div className="mt-4">
          <h2 className="text-sm font-medium text-gray-800">{t("f_language")}</h2>
          <p className="mt-1 text-sm text-gray-700">
            {listing.language
              .map((l) => `${l.lang}${l.level ? ` (${l.level})` : ""}${l.required ? ` - ${t("v_yes")}` : ""}`)
              .join(", ")}
          </p>
        </div>
      )}

      {/* T31 (packet 019): this call's own source document is a scanned/image PDF/DOC with no extractable
          text (OCR stays explicitly out of scope, D6) - said honestly here rather than silently showing an
          empty or misleadingly short requirements/full-text section below. */}
      {listing.content_scanned && (
        <div className="mt-4 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800" data-testid="listing-scanned-notice">
          {t("detail_scanned_notice")}{" "}
          {listing.url && (
            <a href={listing.url} target="_blank" rel="noopener noreferrer nofollow" className="underline">
              {listing.url}
            </a>
          )}
        </div>
      )}

      {listing.requirements_raw && (
        <div className="mt-4">
          <h2 className="text-sm font-medium text-gray-800">{t("detail_requirements")}</h2>
          <p className="mt-1 text-sm text-gray-700 whitespace-pre-line">{listing.requirements_raw}</p>
        </div>
      )}

      {/* T26 (packet 015): the full ad body, not just the requirements excerpt - shown whenever it differs
          from requirements_raw (some sources only ever populate one of the two, never both redundantly). */}
      {listing.raw_text && listing.raw_text !== listing.requirements_raw && (
        <div className="mt-4">
          <h2 className="text-sm font-medium text-gray-800">{t("detail_full_text")}</h2>
          <p className="mt-1 text-sm text-gray-700 whitespace-pre-line" data-testid="listing-full-text">
            {listing.raw_text}
          </p>
        </div>
      )}

      <div className="mt-4">
        <h2 className="text-sm font-medium text-gray-800">{t("detail_contact")}</h2>
        {listing.contact_emails.length > 0 || listing.contact_phones.length > 0 || listing.contact_name ? (
          <p className="mt-1 text-sm text-gray-700 space-x-2" data-testid="listing-contacts">
            {listing.contact_name && <span>{listing.contact_name}</span>}
            {listing.contact_emails.map((e) => (
              <a key={e} href={`mailto:${e}`} className="text-blue-600 hover:underline">
                {e}
              </a>
            ))}
            {listing.contact_phones.map((p) => (
              <a key={p} href={`tel:${p.replace(/[^+\d]/g, "")}`} className="text-blue-600 hover:underline">
                {p}
              </a>
            ))}
          </p>
        ) : (
          <p className="mt-1 text-sm text-gray-400">{t("not_stated")}</p>
        )}
      </div>

      {listing.lat !== null && listing.lon !== null && (
        <div className="mt-4">
          <MapViewClient points={[{ id: listing.id, title: listing.title_raw, lat: listing.lat, lon: listing.lon }]} height={240} />
        </div>
      )}

      <div className="mt-6">
        <h2 className="text-sm font-medium text-gray-800">{t("detail_sources")}</h2>
        <div className="mt-1">
          <SourceChips sources={listing.sources} />
        </div>
      </div>

      {listing.apply_url && (
        <a
          href={listing.apply_url}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="mt-6 inline-block rounded bg-blue-600 text-white px-4 py-2 text-sm hover:bg-blue-700"
        >
          {t("detail_apply")}
        </a>
      )}
    </div>
  );
}
