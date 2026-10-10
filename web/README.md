# Academic Job Platform - web UI (T15, packet 007)

Next.js (App Router, TypeScript) read-only UI. Talks only to the T14 API over HTTP - no direct DB access, no
secrets in the client bundle (see `tests/no-secrets.test.ts`).

## Dev

    cd ..  && python tools/dev_up.py     # embedded pg + migrations + seed + API on :8000 (see ../README.md)
    npm install && npm run dev           # this app on :3000, binds NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 by default

Copy `.env.local.example` to `.env.local` to point at a different API base.

## Structure

* `app/` - the 5 page groups from packet 007: `/` (listings, filters, map), `/listing/[id]` (detail, raw-excerpt
  reveal), `/channels` (external deeplink search providers), `/orgs` + `/orgs/[id]` (institution profiles),
  `/about/methodology`. Plus T16/T17's thesis module: `/thesis` (upload, local-analysis-only note) and
  `/thesis/[id]` (formatting report + `components/SimilaritySection.tsx` - source toggles, Copyleaks consent
  checkbox, estimate/confidence/coverage, verbatim + paraphrase spans with source links and disclaimers).
* `lib/i18n/` - en / sr-Latn / sr-Cyrl dictionaries + a small context provider (browser detection, manual
  switch, localStorage-persisted). UI chrome only - ad text is shown exactly as extracted, with its `ad_lang`.
* `lib/useUrlParams.ts` - the URL query string IS the filter state (13 filters + sort/page), so browser
  back/forward always reproduces the same results.
* `lib/api.ts` / `lib/types.ts` - the only place that talks to the API; types mirror `src/ajp/api/schemas.py`.
* `components/MapView.tsx` / `MapViewClient.tsx` - Leaflet + OSM tiles (no API key), loaded client-only via
  `next/dynamic(..., { ssr: false })` (Leaflet touches `window` at import time).

## Tests

    npm run build     # production build (type-checked)
    npm test          # Vitest + React Testing Library: one render test per page group + the no-secrets guard
    npm run e2e       # Playwright, against a LIVE stack (dev_up.py + npm run dev) - see tests/e2e/flows.spec.ts

`npm run e2e` needs both servers already running (see Dev above); traces/screenshots land in `test-results/`
and an HTML report in `playwright-report/`. `tests/e2e/thesis.spec.ts` uses a small committed sample .docx
(`../tests/thesis_fixtures/e2e_sample_clean.docx`, at the repo root) rather than regenerating one via Python
on every Playwright run - see that spec file's own comment.

## Known gaps (reported, not hidden - see `/about/methodology` and `/v1/meta.degraded_features`)

* Discipline filter/facet is a flat EuroSciVoc-code match; no hierarchy ("include descendants") because no
  authoritative EuroSciVoc concept scheme has been loaded yet.
* City filter uses a static list built from ingested rows' own city values, not a GeoNames typeahead - exactly
  as packet 007 permits deferring to M3.
