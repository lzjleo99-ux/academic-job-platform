"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import { countryFlag } from "@/lib/format";
import type { OrgSummary } from "@/lib/types";

export default function OrgsPage() {
  const { t } = useI18n();
  const [orgs, setOrgs] = useState<OrgSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.orgs().then(setOrgs).catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <h1 className="text-2xl font-semibold text-gray-900">{t("nav_orgs")}</h1>
      {error && <p className="mt-4 text-red-600 text-sm">{t("error_loading")}</p>}
      {!orgs && !error && <p className="mt-4 text-sm text-gray-500">{t("loading")}</p>}
      <ul className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3" data-testid="org-list">
        {(orgs ?? []).map((o) => (
          <li key={o.id}>
            <Link href={`/orgs/${o.id}`} className="block rounded-lg border border-gray-200 p-4 hover:shadow-md transition-shadow">
              <h2 className="font-medium text-gray-900">
                {countryFlag(o.country)} {o.name_en || o.name_native}
              </h2>
              <p className="text-sm text-gray-600">
                {o.city ? `${o.city} · ` : ""}
                {o.type}
              </p>
              <p className="mt-1 text-sm text-blue-700">
                {o.open_listing_count} {t("orgs_open_positions")}
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
