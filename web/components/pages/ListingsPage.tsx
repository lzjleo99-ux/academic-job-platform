"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import { useUrlParams } from "@/lib/useUrlParams";
import type { FacetsResponse, ListingsResponse, MetaResponse } from "@/lib/types";
import { FilterSidebar } from "@/components/FilterSidebar";
import { ListingCard } from "@/components/ListingCard";
import { Pagination } from "@/components/Pagination";
import { MapViewClient } from "@/components/MapViewClient";

const SORTS = ["posted_desc", "deadline_asc", "salary_desc"] as const;

export default function ListingsPage() {
  const { t } = useI18n();
  const { params, setSingle, toggleInList, setList, setPage, clearAll } = useUrlParams();
  const [listings, setListings] = useState<ListingsResponse | null>(null);
  const [facets, setFacets] = useState<FacetsResponse | null>(null);
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showMap, setShowMap] = useState(false);

  useEffect(() => {
    api.meta().then(setMeta).catch(() => void 0);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([api.listings(params), api.facets(params)])
      .then(([l, f]) => {
        if (cancelled) return;
        setListings(l);
        setFacets(f);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [params]);

  const sort = params.get("sort") || "posted_desc";
  const points = (listings?.results ?? [])
    .filter((l): l is typeof l & { lat: number; lon: number } => l.lat !== null && l.lon !== null)
    .map((l) => ({ id: l.id, title: l.title_raw, lat: l.lat, lon: l.lon }));

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 flex flex-col lg:flex-row gap-6">
      <FilterSidebar
        params={params} facets={facets} meta={meta}
        setSingle={setSingle} toggleInList={toggleInList} setList={setList} clearAll={clearAll}
      />

      <main className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
          <div className="text-sm text-gray-600">
            {listings ? `${listings.page.total} ${t("results_count")}` : t("loading")}
          </div>
          <div className="flex items-center gap-3 text-sm">
            <label className="flex items-center gap-1">
              {t("sort_label")}
              <select
                value={sort}
                onChange={(e) => setSingle("sort", e.target.value)}
                className="rounded border-gray-300 text-sm"
              >
                {SORTS.map((s) => (
                  <option key={s} value={s}>
                    {t(("sort_" + s) as never)}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => setShowMap((v) => !v)}
              className="rounded border border-gray-300 px-2 py-1"
              aria-pressed={showMap}
            >
              {showMap ? "☰" : "ὟA"} map
            </button>
            <a href={api.exportUrl(params, "csv")} className="text-blue-600 hover:underline">
              {t("export_csv")}
            </a>
            <a href={api.exportUrl(params, "json")} className="text-blue-600 hover:underline">
              {t("export_json")}
            </a>
          </div>
        </div>

        {error && <p className="text-red-600 text-sm mb-4">{t("error_loading")}</p>}

        {showMap && (
          <div className="mb-4">
            <MapViewClient points={points} />
          </div>
        )}

        {listings && listings.results.length === 0 && !error && (
          <p className="text-gray-500 text-sm">{t("no_results")}</p>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4" data-testid="listing-results">
          {(listings?.results ?? []).map((l) => (
            <ListingCard key={l.id} listing={l} />
          ))}
        </div>

        {listings && <Pagination page={listings.page} onChange={setPage} />}
      </main>
    </div>
  );
}
