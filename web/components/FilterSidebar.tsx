"use client";
import { useI18n } from "@/lib/i18n/context";
import type { FacetsResponse, MetaResponse } from "@/lib/types";
import { countryFlag, countryName } from "@/lib/format";

interface Props {
  params: URLSearchParams;
  facets: FacetsResponse | null;
  meta: MetaResponse | null;
  setSingle: (key: string, value: string | number | boolean | undefined | null) => void;
  toggleInList: (key: string, value: string) => void;
  setList: (key: string, values: string[]) => void;
  clearAll: () => void;
}

function CheckboxGroup({
  legend,
  options,
  selected,
  onToggle,
  labelFor,
}: {
  legend: string;
  options: { value: string; count?: number }[];
  selected: string[];
  onToggle: (v: string) => void;
  labelFor?: (v: string) => string;
}) {
  if (options.length === 0) return null;
  return (
    <fieldset className="mb-4">
      <legend className="text-sm font-medium text-gray-800 mb-1">{legend}</legend>
      <div className="space-y-1 max-h-40 overflow-y-auto pr-1">
        {options.map((o) => (
          <label key={o.value} className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input
              type="checkbox"
              checked={selected.includes(o.value)}
              onChange={() => onToggle(o.value)}
              className="rounded border-gray-300"
            />
            <span>{labelFor ? labelFor(o.value) : o.value}</span>
            {o.count !== undefined && <span className="text-gray-400 text-xs">({o.count})</span>}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

// T33 (packet 018, Q6): the API is unfiltered (all real kinds) by default now - PM's own real-DB audit found
// the previous employment-only default hid 1,280 of 3,948 open listings (grant_call/internship/enrollment/
// other) from every fresh visitor. Kept as a real, importable list (not re-typed at each call site) so the
// checkbox group's own default-checked state can never silently drift from what the API actually does.
const ALL_KINDS = ["employment", "enrollment", "grant_call", "internship", "other"];

export function FilterSidebar({ params, facets, meta, setSingle, toggleInList, setList, clearAll }: Props) {
  const { t } = useI18n();
  const getAll = (k: string) => params.getAll(k);
  // The checkbox group must show the SAME default the API applies (every kind, unfiltered) as checked, and
  // toggling anything makes it explicit rather than XOR-ing against an empty underlying param (which would
  // otherwise silently drop whichever kinds were only ever implicitly selected).
  const kindSelected = getAll("kind").length ? getAll("kind") : ALL_KINDS;
  const toggleKind = (v: string) => {
    const next = kindSelected.includes(v) ? kindSelected.filter((x) => x !== v) : [...kindSelected, v];
    setList("kind", next);
  };

  return (
    <aside className="w-full lg:w-72 shrink-0 space-y-1" aria-label={t("filters_title")}>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-lg font-semibold">{t("filters_title")}</h2>
        <button type="button" onClick={clearAll} className="text-sm text-blue-600 hover:underline">
          {t("filters_clear")}
        </button>
      </div>

      <CheckboxGroup
        legend={t("f_country")}
        options={(facets?.country ?? []).map((b) => ({ value: b.value, count: b.count }))}
        selected={getAll("country")}
        onToggle={(v) => toggleInList("country", v)}
        labelFor={(v) => `${countryFlag(v)} ${countryName(v, "en")}`}
      />

      <CheckboxGroup
        legend={t("f_city")}
        options={(facets?.city ?? []).map((b) => ({ value: b.value, count: b.count }))}
        selected={getAll("city")}
        onToggle={(v) => toggleInList("city", v)}
      />

      {/* T23 (PM ruling R2): 'Remote/Unknown' facet - a listing whose country couldn't be resolved (or is
          genuinely remote/hybrid) is never dropped or guessed into a country; this is how it stays findable. */}
      <CheckboxGroup
        legend={t("f_location_class")}
        options={["located", "remote", "hybrid", "unknown"].map((v) => ({
          value: v, count: facets?.location_class?.find((b) => b.value === v)?.count ?? 0,
        }))}
        selected={getAll("location_class")}
        onToggle={(v) => toggleInList("location_class", v)}
        labelFor={(v) => t(("v_" + v) as never)}
      />

      <CheckboxGroup
        legend={t("f_kind")}
        options={(facets?.kind ?? []).map((b) => ({ value: b.value, count: b.count }))}
        selected={kindSelected}
        onToggle={toggleKind}
        labelFor={(v) => t((("k_" + v) as never))}
      />

      <CheckboxGroup
        legend={t("f_position_kind")}
        options={(meta?.position_kind ?? []).map((v) => ({
          value: v,
          count: facets?.position_kind.find((b) => b.value === v)?.count ?? 0,
        }))}
        selected={getAll("position_kind")}
        onToggle={(v) => toggleInList("position_kind", v)}
      />

      <CheckboxGroup
        legend={t("f_foreigner")}
        options={["yes", "no", "unknown"].map((v) => ({
          value: v, count: facets?.foreigner.find((b) => b.value === v)?.count ?? 0,
        }))}
        selected={getAll("foreigner")}
        onToggle={(v) => toggleInList("foreigner", v)}
        labelFor={(v) => t(("v_" + v) as never)}
      />

      <fieldset className="mb-4">
        <legend className="text-sm font-medium text-gray-800 mb-1">{t("f_salary")}</legend>
        <div className="flex gap-2">
          <input
            type="number" placeholder="min" aria-label={`${t("f_salary")} min`}
            defaultValue={params.get("salary_min") ?? ""}
            onBlur={(e) => setSingle("salary_min", e.target.value || undefined)}
            className="w-1/2 rounded border-gray-300 text-sm"
          />
          <input
            type="number" placeholder="max" aria-label={`${t("f_salary")} max`}
            defaultValue={params.get("salary_max") ?? ""}
            onBlur={(e) => setSingle("salary_max", e.target.value || undefined)}
            className="w-1/2 rounded border-gray-300 text-sm"
          />
        </div>
        <label className="mt-1 flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
          <input
            type="checkbox"
            checked={params.get("salary_include_unspecified") !== "false"}
            onChange={(e) => setSingle("salary_include_unspecified", e.target.checked ? undefined : "false")}
            className="rounded border-gray-300"
          />
          {t("f_salary_unspecified")}
        </label>
      </fieldset>

      <CheckboxGroup
        legend={t("f_duration")}
        options={(meta?.duration_buckets ?? []).map((v) => ({
          value: v, count: facets?.duration_bucket.find((b) => b.value === v)?.count ?? 0,
        }))}
        selected={getAll("duration_bucket")}
        onToggle={(v) => toggleInList("duration_bucket", v)}
        labelFor={(v) => (v === "permanent" ? t("v_permanent") : v)}
      />

      <CheckboxGroup
        legend={t("f_ft_pt")}
        options={(meta?.ft_pt ?? []).map((v) => ({
          value: v, count: facets?.ft_pt.find((b) => b.value === v)?.count ?? 0,
        }))}
        selected={getAll("ft_pt")}
        onToggle={(v) => toggleInList("ft_pt", v)}
        labelFor={(v) => t(("v_" + v) as never)}
      />

      <CheckboxGroup
        legend={t("f_study_while_working")}
        options={["true", "false", "unknown"].map((v) => ({ value: v }))}
        selected={getAll("study_while_working")}
        onToggle={(v) => toggleInList("study_while_working", v)}
        labelFor={(v) => t(("v_" + v) as never)}
      />

      <CheckboxGroup
        legend={t("f_language")}
        options={(facets?.language ?? []).map((b) => ({ value: b.value, count: b.count }))}
        selected={getAll("language")}
        onToggle={(v) => toggleInList("language", v)}
      />

      <CheckboxGroup
        legend={t("f_degree")}
        options={(meta?.degree_required ?? []).map((v) => ({
          value: v, count: facets?.degree_required.find((b) => b.value === v)?.count ?? 0,
        }))}
        selected={getAll("degree_required")}
        onToggle={(v) => toggleInList("degree_required", v)}
      />

      <fieldset className="mb-4">
        <legend className="text-sm font-medium text-gray-800 mb-1">{t("f_deadline")}</legend>
        <select
          aria-label={t("f_deadline")}
          value={params.get("deadline_bucket") ?? ""}
          onChange={(e) => setSingle("deadline_bucket", e.target.value || undefined)}
          className="w-full rounded border-gray-300 text-sm"
        >
          <option value="">{"—"}</option>
          <option value="closing_soon">{t("deadline_closing_soon")}</option>
          <option value="this_month">{t("deadline_this_month")}</option>
        </select>
      </fieldset>

      <CheckboxGroup
        legend={t("f_source")}
        options={(facets?.source ?? []).map((b) => ({ value: b.value, count: b.count }))}
        selected={getAll("source")}
        onToggle={(v) => toggleInList("source", v)}
      />

      <fieldset className="mb-4">
        <legend className="text-sm font-medium text-gray-800 mb-1">{t("f_discipline")}</legend>
        <div className="space-y-1 max-h-40 overflow-y-auto pr-1">
          {(meta?.disciplines_euroscivoc ?? []).map((d) => (
            <label key={d.code} className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input
                type="checkbox"
                checked={getAll("discipline").includes(d.code)}
                onChange={() => toggleInList("discipline", d.code)}
                className="rounded border-gray-300"
              />
              <span>{d.code}</span>
              <span className="text-gray-400 text-xs">({d.n})</span>
            </label>
          ))}
        </div>
        {meta && !meta.disciplines_taxonomy_complete && (
          <p className="mt-1 text-xs text-gray-400">
            {/* transparent about the known gap rather than pretending this is a full taxonomy tree */}
            {meta.degraded_features.find((f) => f.feature === "discipline_hierarchy")?.reason}
          </p>
        )}
      </fieldset>

      <fieldset className="mb-4">
        <legend className="text-sm font-medium text-gray-800 mb-1">{t("f_text")}</legend>
        <input
          type="search" aria-label={t("f_text")}
          defaultValue={params.get("text") ?? ""}
          onBlur={(e) => setSingle("text", e.target.value || undefined)}
          className="w-full rounded border-gray-300 text-sm"
        />
      </fieldset>
    </aside>
  );
}
