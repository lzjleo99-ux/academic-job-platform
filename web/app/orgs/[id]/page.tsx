"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import { countryFlag } from "@/lib/format";
import type { OrgDetail } from "@/lib/types";

export default function OrgDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t } = useI18n();
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.org(id).then(setOrg).catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [id]);

  if (error) return <div className="mx-auto max-w-3xl px-4 py-6 text-red-600 text-sm">{t("error_loading")}</div>;
  if (!org) return <div className="mx-auto max-w-3xl px-4 py-6 text-sm text-gray-500">{t("loading")}</div>;

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <Link href="/orgs" className="text-sm text-blue-600 hover:underline">
        {"←"} {t("nav_orgs")}
      </Link>
      <h1 className="mt-2 text-2xl font-semibold text-gray-900">
        {countryFlag(org.country)} {org.name_en || org.name_native}
      </h1>
      <p className="text-gray-600 mt-1">
        {org.city ? `${org.city}, ` : ""}
        {org.country} {"·"} {org.type}
      </p>

      <p className="mt-2 text-blue-700 text-sm font-medium">
        {org.open_listing_count} {t("orgs_open_positions")}
      </p>

      {org.homepage && (
        <a href={org.homepage} target="_blank" rel="noopener noreferrer" className="mt-3 inline-block text-sm text-blue-600 hover:underline">
          {t("orgs_visit")} {"→"}
        </a>
      )}

      {org.career_urls.length > 0 && (
        <div className="mt-4">
          <h2 className="text-sm font-medium text-gray-800">Career / board pages</h2>
          <ul className="mt-1 space-y-1">
            {org.career_urls.map((u) => (
              <li key={u}>
                <a href={u} target="_blank" rel="noopener noreferrer" className="text-sm text-blue-600 hover:underline break-all">
                  {u}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
        <div>
          <dt className="text-gray-500">Crawl status</dt>
          <dd>{org.robots_status ?? t("value_unknown")}</dd>
        </div>
        <div>
          <dt className="text-gray-500">Render mode</dt>
          <dd>{org.render_mode ?? t("value_unknown")}</dd>
        </div>
        {org.openalex_works_count !== null && (
          <div>
            <dt className="text-gray-500">OpenAlex works</dt>
            <dd>{org.openalex_works_count}</dd>
          </div>
        )}
        {(org.qs_rank || org.the_rank) && (
          <div>
            <dt className="text-gray-500">Ranking</dt>
            <dd>
              {org.qs_rank && `QS ${org.qs_rank}`} {org.the_rank && `THE ${org.the_rank}`}
            </dd>
          </div>
        )}
      </dl>
    </div>
  );
}
