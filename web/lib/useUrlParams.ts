"use client";
// The URL query string IS the filter state (13 filters + sort/page) - no separate client-only store to fall
// out of sync with it, so browser back/forward always reproduces exactly the results that were showing (the
// acceptance's "URL-synced filters ... back/forward safe"). Every page that filters reuses this one hook.
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

export function useUrlParams() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const params = useMemo(() => new URLSearchParams(searchParams.toString()), [searchParams]);

  const push = useCallback(
    (next: URLSearchParams) => {
      next.delete("page"); // any filter change resets pagination - a stale page number over new results is confusing
      router.push(`${pathname}?${next.toString()}`, { scroll: false });
    },
    [router, pathname],
  );

  const setSingle = useCallback(
    (key: string, value: string | number | boolean | undefined | null) => {
      const next = new URLSearchParams(params.toString());
      if (value === undefined || value === null || value === "") next.delete(key);
      else next.set(key, String(value));
      push(next);
    },
    [params, push],
  );

  /** Toggle `value` in/out of the multi-valued query param `key` (checkboxes/chip groups). */
  const toggleInList = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      const current = next.getAll(key);
      next.delete(key);
      const without = current.filter((v) => v !== value);
      const updated = without.length === current.length ? [...current, value] : without;
      for (const v of updated) next.append(key, v);
      push(next);
    },
    [params, push],
  );

  /** Replace `key`'s whole value list at once - used where a default (e.g. kind=employment when the param is
   * absent) must become explicit the moment the user changes anything, rather than XOR-ing against an empty
   * underlying param (see FilterSidebar's kind group). */
  const setList = useCallback(
    (key: string, values: string[]) => {
      const next = new URLSearchParams(params.toString());
      next.delete(key);
      for (const v of values) next.append(key, v);
      push(next);
    },
    [params, push],
  );

  const setPage = useCallback(
    (page: number) => {
      const next = new URLSearchParams(params.toString());
      if (page <= 1) next.delete("page");
      else next.set("page", String(page));
      router.push(`${pathname}?${next.toString()}`, { scroll: true });
    },
    [params, router, pathname],
  );

  const clearAll = useCallback(() => {
    router.push(pathname, { scroll: false });
  }, [router, pathname]);

  return { params, setSingle, toggleInList, setList, setPage, clearAll };
}
