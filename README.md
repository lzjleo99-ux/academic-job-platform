# academic-job-platform

Academic job listing aggregator (Serbia + international expansion) + a thesis formatting/similarity module.
Localhost-only in this milestone (RC1) - no production deployment is built or documented as ready to use.

## Architecture / data flow

    connectors (ajp/connectors/*.py, one per source family)
      -> PoliteClient (robots.txt, 1 req/s/host default, identifying User-Agent, TLS always verified)
      -> ajp.rawstore (content-addressed raw HTTP envelope store - every extracted field traces back to a
         real fetched byte string, never re-derived from nothing)
      -> ajp.ingest.upsert_listing_deduped (cross-source dedupe, D6: unresolved fields stay null+confidence+
         raw excerpt, never fabricated)
      -> Postgres (embedded via pgserver by default; src/ajp/migrations/*.sql, forward-only & checksummed)
      -> ajp.scheduler (T21: due-source cadence+jitter, run_ledger evidence trail, R1 3-strikes blocking)
      -> ajp.api (read-only FastAPI, /v1/listings /v1/facets /v1/orgs /v1/sources /v1/governance /v1/coverage
         /health /thesis/*) -> web/ (Next.js UI, talks to the API over HTTP only, no direct DB access)

`ajp.bootstrap` is the single source of truth for "seed everything a fresh install needs" (org backbone,
every connector's own `ensure_source()`, every deeplink channel, FX fixture rates, and - opt-in only, see
below - a small labelled demo listings fixture) - both `tools/dev_up.py` and the packaged `ajp-init` console
script call it, so a new seed never has to be added in two places to actually take effect everywhere.

## Clean-room bootstrap (fresh venv, no repo-specific setup beyond a checkout)

    python3 -m venv .venv && . .venv/bin/activate
    python -m pip install --upgrade pip        # REQUIRED: pip < ~21.3 cannot do a pyproject.toml-only
                                                 # editable install at all (found live doing this exact test -
                                                 # a fresh venv's own bundled pip failed with "editable mode
                                                 # currently requires a setuptools-based build" until upgraded)
    pip install -e ".[dev]"                     # add "[dev]" only if you also want to run the test suite
    playwright install chromium                 # only if you'll use ajp.thesis's own browser-rendered
                                                 # extraction path or web/'s Playwright E2E suite (T23) -
                                                 # skip it otherwise, nothing else needs it
    ajp-init                                    # embedded pg + migrations + ALL seeds (186 RS orgs: 100 base
                                                 # + 86 OpenAlex-reconciliation supplement, counts asserted,
                                                 # fails loudly on drift). Idempotent - safe to re-run. No demo
                                                 # data by default (T32) - add --with-demo or AJP_LOAD_DEMO=1
                                                 # for the small [SAMPLE]-labelled demo listings fixture.
    ajp-api                                     # http://127.0.0.1:8000/docs - binds 127.0.0.1 only, not a flag

`ajp-init`/`ajp-run`/`ajp-api` all default their own data directory to `~/.ajp` (override with
`--data-dir PATH` or `AJP_DATA_DIR`); each starts its own embedded Postgres against whatever is already on
disk there, so re-running any of them against the same `--data-dir` picks up right where the last one left
off. Copy `.env.example` to `.env` for the optional settings (contact address, thesis-similarity API keys) -
none are required for the above to work.

    python -m pytest -m "not live"              # offline suite (embedded Postgres via pgserver, no setup)

## Running the scheduler (T21)

    ajp-run --once                              # one pass: FX snapshot first, then every due source,
                                                 # each isolated (one failure never stops the others)
    ajp-run --daemon                            # loops until Ctrl-C/SIGTERM; cadence defaults to 24h +/-10%
                                                 # deterministic jitter per source per day (R4); the same
                                                 # per-host 1 req/s limit is enforced across every source
                                                 # that happens to share a host, not per-connector

Every run writes one `run_ledger` row per source it touches (`SELECT * FROM run_ledger` for the evidence
trail: pages fetched, rows new/updated, blocked reason, http counters) - inspect it directly via `psql`, or
through `GET /v1/governance` / `GET /v1/coverage` on the API. A source failing 3 consecutive scheduled runs
is logged `status='blocked'` (crawl_state.py's own R1 counter, reused here at the source level); a process
killed mid-run leaves that one row `status='error'`/`note='interrupted...'` (closed out automatically at the
start of the next `run_once()` call, never left dangling forever).

## Running the web UI (T15, alongside the API)

    cd web && npm install && npm run dev        # http://localhost:3000, talks to the API at :8000 by default
                                                 # (see web/.env.local.example to point it elsewhere)

No single combined "start everything" command exists yet beyond running `ajp-api` and `cd web && npm run dev`
in two terminals (or two background jobs) - see `web/README.md` for the UI's own structure/tests, and
`lib/i18n/` for its own EN / Serbian-Latin / Serbian-Cyrillic locale dictionaries. Both bind to `127.0.0.1`
only.

**Open the UI at `http://localhost:3000`, not `http://127.0.0.1:3000`** - found live during RC2 acceptance:
the Next.js 16/Turbopack dev server's own RSC+HMR transport stalls on the `127.0.0.1` origin specifically
(client effects never fire, the results grid stays empty) even though the server is listening and the API
itself is reachable either way. If the page seems "stuck" (an HMR handshake that breaks after a stop/
restart shows the exact same symptom, `ERR_INVALID_HTTP_RESPONSE` in the console), restart
`npm run dev` - it is not a data problem.

## Always-on, automatic sync (T28, macOS user LaunchAgents)

Per the user's own explicit demand - this platform must stay in sync with online jobs automatically, never
depending on a human running a command by hand - `tools/install_agents.sh` installs 3 **user-level** (not
system-level; no `sudo`, no root daemon) macOS LaunchAgents:

* `com.ajp.scheduler` - `ajp-run --daemon` (the existing scheduler above: 24h ±10% jitter cadence, the R1
  3-strikes blocking counter, the F9 per-source watchdog, every deeplink/robots rule unchanged).
* `com.ajp.api` - `ajp-api` on its usual `127.0.0.1:8000`.
* `com.ajp.web` - the Next.js UI on `127.0.0.1:3000` (a real production `npm run build` + `npm run start`
  when the build succeeds at install time; falls back to `npm run dev` and says so if it doesn't).

All 3 **start automatically at login** (`RunAtLoad`) and **restart automatically after a real crash** -
never after a clean, intentional exit, and never more than once every 10 minutes (`ThrottleInterval 600`, a
safety margin against a hard crash loop). Logs: `~/.ajp/logs/{scheduler,api,web}.log`.

    ./tools/install_agents.sh      # verifies paths, seeds/upgrades the DB, builds the web bundle, installs
                                    # and starts all 3 agents, ends with PASS/FAIL health checks
    ./tools/uninstall_agents.sh    # boots out all 3 and removes their plists - NEVER touches ~/.ajp data

Both scripts are idempotent (safe to re-run after a code update) and print `PASS`/`FAIL` for every step,
never failing silently. `tools/launchd/*.plist` are committed as templates with `__AJP_...__` placeholder
tokens (a plist cannot expand a shell variable or `~`) - the install script resolves this machine's own
absolute venv/Node paths and substitutes them; never edit an installed plist under
`~/Library/LaunchAgents/` by hand, edit the template and re-run the install script instead.

**Pause vs. stop**: `launchctl kill SIGTERM gui/$(id -u)/com.ajp.<name>` stops a service for a moment, but
`KeepAlive` relaunches it (after the 10-minute throttle) since a kill is not treated as a "successful exit" -
useful to force an immediate restart, not a real pause. `launchctl bootout gui/$(id -u)/com.ajp.<name>` (or
`./tools/uninstall_agents.sh` for all three) is the real stop: it will not relaunch and will not start again
at the next login until re-installed.

## Repository layout

* `src/ajp/migrations/` - data_contract tables (`source`, `org_master`, `listing`), `fx_rate`, `run_ledger`
  (T21), `discovery_state` (T20), views `fx_rate_latest` (precedence NBS > ECB > ERAPI) and
  `listing_salary_eur_year`.
* `src/ajp/connectors/` - one module per source family (bespoke aggregators, ATS families, generic
  WordPress/sitemap engines); `src/ajp/scheduler.py` (T21) is the single factory reconstructing a real
  connector instance from a `source` row's own persisted identity.
* `src/ajp/bootstrap.py` - the seeding source of truth (see Architecture above); `src/ajp/cli/` - the
  `ajp-init`/`ajp-run`/`ajp-api` console scripts.
* `src/ajp/rawstore.py` - object-store key layout for raw HTTP/JSON/XML (documented in the module).
* `src/ajp/robots.py` - RFC 9309 robots guard; every connector must call it before fetching (D5).
* `src/ajp/api/` - read-only FastAPI listing API; `app.py` is the ASGI entrypoint.
* `src/ajp/thesis/` - thesis formatting checks + similarity estimate: `parser.py` (PDF/DOCX -> `ThesisDoc`),
  `templates/` (YAML, `rs_phd_generic.yaml` is explicitly generic, never an official faculty rulebook),
  `checks.py` (structural/style checks), `similarity/` (normalize -> segment -> fingerprint -> match ->
  score; see `docs/similarity_formula.md` for the full transparent formula and D4's standing ruling against
  ever presenting this as a "plagiarism %").
* `src/ajp/applicants/` (T25) - CV upload + matching: `profile_extract.py` (reuses `ajp.thesis.parser` for
  the actual PDF/DOCX text extraction, then CV-specific structured extraction on top - disciplines/degree/
  skills/languages/location & salary preferences), `matching.py` (the deterministic, documented
  `MATCH_WEIGHTS` scoring engine, rendered live on the methodology page - never hand-restated), `store.py`
  (mirrors `ajp.thesis.store`'s own local-only, real-delete convention).
* `web/` - Next.js UI (see `web/README.md`).
* `tools/dev_up.py` - a thin CLI wrapper around `ajp.bootstrap` for a repo-checkout dev loop (same seeding as
  `ajp-init`, plus starting the API in the same process for convenience).
* `tools/launchd/`, `tools/install_agents.sh`, `tools/uninstall_agents.sh` (T28) - always-on macOS
  LaunchAgents; see "Always-on, automatic sync" above.
* `docs/index.html` - the static GitHub Pages landing page (source = `main`, folder = `/docs`); see
  "Honest scope" below for why it is static and shows no live data. `docs/matching_formula.md` /
  `docs/similarity_formula.md` are the real, authoritative formula docs the code itself links back to.
* `tests/test_repo_hygiene.py` (T42) - scans the actual `git ls-files` tracked set for a secret-shaped
  string or a hardcoded local absolute path; fails the suite, not just a human review, if one slips in.
* `pm/outbox/operator_actions.md` (referenced, not in this repo - see the project's own PM channel) - things
  only a human can do (API keys, account registrations); everything in this README works without any of them.

## How to add a source/org (template)

1. **A new RS org with a real career board**: add a row to `src/ajp/seeds/rs_orgs.json` (slug, name, homepage,
   career_urls once known) or, if discovered via `ajp.discovery`, let it land in `discovery_state` first and
   promote it deliberately (never auto-promoted without a real dry-run's schema-valid rows as evidence).
2. **A new org matching an EXISTING connector family** (ATS-hosted, WordPress, sitemap-driven): call that
   family's own `ensure_source()` with the tenant/config you've verified live (never guessed - see
   `ajp.connectors.ats_detect` for how a family is recognised from an org's own careers page), then add that
   call to `ajp.bootstrap.ensure_sources()` - `tests/test_bootstrap_registers_every_connector.py` fails the
   build if a connector with its own `ensure_source()` is ever left unregistered there.
3. **A genuinely new source shape**: build the connector (discover()+crawl(items) is the common shape;
   `ajp.scheduler._discover_and_crawl` also accepts a connector whose own `crawl()` needs no arguments at
   all - see its own docstring for which existing connectors use which shape), add live-verified tests, wire
   `ensure_source()` into `ajp.bootstrap`, and add the new import to `pyproject.toml`'s own dependencies if
   it needs one (`tests/test_dependencies_declared.py` fails the build otherwise).
4. **A new import**: declare it in `pyproject.toml` (main `dependencies`, or an `[project.optional-dependencies]`
   extra if it is genuinely optional, e.g. `similarity`/`browser`) - see `tests/test_dependencies_declared.py`.

## Notes

* er-api rates require the attribution link "Rates By Exchange Rate API" wherever a converted figure is
  shown, and the raw rate table must not be redistributed.
* With `pg_trgm` available the trigram indexes (migration 0005) are created; that branch could not be
  exercised on the embedded test server, which lacks the extension.
* The API's own EUR/year salary conversion (hour x40x52, stipend annualised over the ad's stated duration) is
  a documented assumption, not an extracted fact - `/v1/listings` always reports the fx basis (rate
  date/source) alongside the figure, and `/v1/meta.degraded_features` lists what is not yet implemented (a
  full EuroSciVoc concept tree; GeoNames city typeahead), rather than silently presenting a partial feature
  as complete.
* Known real, live-verified blocked/degraded hosts (not bugs on this project's own side): `inst-imi`/
  `nis-eknfak` (TLS certificate verification failures on the org's own server), `bg-rgf` (a soft-404 host -
  every path returns 200, correctly detected and never trusted by `ajp.discovery`'s own control-probe),
  `bg-fsfv` (robots.txt `Disallow: /`), `private-alfa` (a real, reproducible HTTP/2 server bug on
  alfa.edu.rs). See `pm/outbox/release_rc1.md` (T24) for the full, honest list.

## Honest scope - what this project deliberately does NOT claim to be

* **Not a public, hosted service.** Everything here is localhost-only; no production deployment is built
  or documented as ready to use in this milestone. Running it means cloning this repo and starting it on
  your own machine (see "Clean-room bootstrap" above) - there is no publicly reachable live instance.
* **The thesis similarity estimate is not Turnitin** (or any other commercial plagiarism-detection
  service) - `ajp.thesis.similarity` is a documented, transparent heuristic over a small, explicitly named
  set of real sources (an internal corpus of previously-uploaded theses, OpenAlex, Crossref, Semantic
  Scholar, Unpaywall, and - only with the student's own opted-in credentials - Copyleaks). It is never
  presented as a "plagiarism %" or an authoritative verdict (D4's own standing ruling); see
  `docs/similarity_formula.md` for the full formula and what it deliberately does not do.
* **Blocked or robots-disallowed sources stay deeplink-only, never scraped around.** A source this
  project cannot legitimately crawl (a TLS failure, a real `robots.txt` disallow, a known soft-404 host)
  is linked out to directly and honestly, with the real reason recorded (`ajp.discovery`, `ajp.robots`) -
  never silently bypassed.
* **No OCR.** A scanned PDF/image with no extractable text is flagged honestly (`content_scanned` on the
  listing, a real UI banner) rather than silently skipped or its content guessed/fabricated.
* **A small, static GitHub Pages site** (`docs/index.html`, enable Pages with source = `main` branch,
  `/docs` folder) exists purely as a public landing page describing the project and linking back to this
  repo and its docs - it makes no live requests and shows no live data, since the actual running platform
  (Postgres + FastAPI + Next.js) cannot be hosted on Pages at all (no server runtime, no database) and is
  not publicly deployed anywhere. See `pm/outbox/t42_report.md` for the full reasoning.

## License

[MIT](LICENSE) - a permissive default for a personal/portfolio project with no current plan to publish a
competing hosted service; change it before publishing if you want different terms (nothing here depends on
MIT specifically).
