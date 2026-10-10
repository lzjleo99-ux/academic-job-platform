"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { ChannelLinkOut, HealthResponse, MetaResponse } from "@/lib/types";

export default function MethodologyPage() {
  const { t } = useI18n();
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [channels, setChannels] = useState<ChannelLinkOut[] | null>(null);
  const [weights, setWeights] = useState<Record<string, number> | null>(null);

  useEffect(() => {
    api.meta().then(setMeta).catch(() => void 0);
    api.health().then(setHealth).catch(() => void 0);
    api.channels().then(setChannels).catch(() => void 0);
    api.matchingWeights().then(setWeights).catch(() => void 0);
  }, []);

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 prose prose-sm">
      <h1 className="text-2xl font-semibold text-gray-900">{t("methodology_title")}</h1>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Where the data comes from</h2>
        <p className="text-sm text-gray-700">
          Listings are collected directly from institutions&apos; own employment boards where possible
          (labelled &ldquo;{t("card_verified_direct")}&rdquo;), from Serbia&apos;s national employment
          service (NSZ), and from a small set of tier-1 international academic-job aggregators (jobs.ac.uk,
          Nature Careers, AcademicTransfer, CERN, CNRS, academicpositions.com, sljaka.com, Inside Higher Ed
          Careers, The Chronicle of Higher Education Jobs). The same posting found on several of these is
          merged into a single listing that lists every place it was seen; no field of the merged listing is
          ever silently overwritten by a later source. Every listing links back to where it was originally
          published - this site never claims authorship of any ad.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Compliance</h2>
        <p className="text-sm text-gray-700">
          Every fetch is gated on that site&apos;s robots.txt and rate-limited (1 request/second by default,
          or that site&apos;s own stated crawl-delay). A small number of sites explicitly prohibit automated
          access, or their robots.txt disallows the pages we would need; those are shown only as
          &ldquo;{t("nav_channels")}&rdquo; - a link that opens a search on the other site directly in your
          browser, never fetched or stored by us. TLS certificate verification is never disabled. See the{" "}
          {t("nav_compliance")} page for this platform&apos;s own real, currently-measured numbers (robots
          state, requests in the last 24h, and a count of denied attempts that should always read 0).
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Update cadence</h2>
        <p className="text-sm text-gray-700">
          Each source is re-checked roughly once every 24 hours, with a small random offset (up to
          &plusmn;10%, fixed per source per day) so every source does not all fetch at the exact same moment.
          A source that fails 3 scheduled runs in a row is marked blocked and stops being retried
          automatically until the underlying issue is understood.
          {" "}
          {health?.fx_as_of
            ? `Currency conversion rates were last updated ${health.fx_as_of}.`
            : "Currency conversion rate freshness is unavailable right now."}
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Salary conversion (EUR/year)</h2>
        <p className="text-sm text-gray-700">
          The EUR/year figure shown on a listing is a computed estimate, not something the ad itself stated -
          the ad&apos;s own original wording is always shown alongside it. The rule: an amount stated per
          year is used as-is; per month is multiplied by 12; per hour is multiplied by 40 hours/week &times;
          52 weeks/year (a stated assumption, since a real weekly-hours figure is rarely given); a stipend is
          annualised over the position&apos;s own stated duration (amount &divide; duration &times; 12) - and
          left blank, never guessed, if no duration was stated. A non-EUR currency is converted using the
          exchange rate on file for that currency (preferring the National Bank of Serbia, then the European
          Central Bank, then a public rates API) as of the date shown; if no rate is on file for that
          currency, the EUR/year figure is left blank rather than computed with a guessed rate.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Location: located, remote, hybrid, unknown</h2>
        <p className="text-sm text-gray-700">
          Most ads state a country and city directly. When one doesn&apos;t, the country is never guessed -
          instead the ad&apos;s own text is checked for an explicit remote/hybrid work-arrangement phrase
          (e.g. &ldquo;fully remote&rdquo;, &ldquo;hybrid role&rdquo;); if one is found, the listing is
          labelled accordingly, with the exact phrase kept alongside it. If neither a location nor such a
          phrase is present, the listing is labelled &ldquo;{t("v_unknown")}&rdquo; - visible and filterable,
          never dropped from the results just because its location couldn&apos;t be determined.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Confidence &amp; missing values</h2>
        <p className="text-sm text-gray-700">
          A value we are not certain about (duration, salary, whether you can study while working, ...)
          always keeps the exact excerpt from the ad it was read from, and a rough confidence score - click
          the small dot next to such a value anywhere on the site to see both. A field we could not find in
          the ad is left blank rather than guessed. The confidence score is a rough, source-specific
          estimate of how directly the value was stated versus inferred from surrounding text - not a
          statistical measure computed the same way across every field; a higher number means the original
          wording was closer to a direct statement of that value.
        </p>
      </section>

      {channels && channels.length > 0 && (
        <section className="mt-4">
          <h2 className="text-lg font-medium text-gray-900">Sources shown only as a search link</h2>
          <p className="text-sm text-gray-700">
            The following sites are never fetched or stored by us at all - each one opens a real search on
            that site directly in your browser instead, usually because that site&apos;s own robots.txt or
            terms prohibit automated access to the pages we would need:
          </p>
          <ul className="text-sm text-gray-700">
            {channels.map((c) => (
              <li key={`${c.source_id}-${c.slug}`}>
                {c.label}
                {c.description && <span className="text-gray-500"> — {c.description}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Known gaps</h2>
        <ul className="text-sm text-gray-700">
          {(meta?.degraded_features ?? []).map((f) => (
            <li key={f.feature}>
              <strong>{f.feature}</strong>: {f.reason}
            </li>
          ))}
        </ul>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Applicant CV matching ({t("nav_applicants")})</h2>
        <p className="text-sm text-gray-700">
          A CV you upload is analysed entirely on this server (see the Privacy page); the structured profile
          extracted from it - disciplines, degree/career stage, skills, languages, desired countries/cities,
          remote/full-part-time preference, years of experience, minimum salary, and any stated visa note -
          is heuristic and always yours to review and correct before matching runs. The match score (0-100)
          against each open listing is the sum of these committed weights - but a criterion neither side
          states enough to compare is never scored as a bad fit (0) and never simply dropped either: it
          contributes a neutral 0.5 fraction of its own weight, so a listing that states almost nothing
          converges toward the neutral midpoint (~50), a listing that genuinely matches on what it states can
          still reach 100, and a listing that actively mismatches on what it states falls below 50. Every
          match also reports a <strong>coverage</strong> figure - the share of the total weight backed by
          real, positive evidence specifically - so a sparse ~50 (mostly &quot;we don&apos;t know&quot;) is
          never mistaken for a genuinely well-evidenced match at the same number; each criterion&apos;s own
          state (matched / mismatched / unknown) and reason is always shown alongside the score, never a bare
          number. A criterion is only ever marked &quot;mismatched&quot; on real, positive evidence of an
          actual mismatch - a listing whose own topic or requirements simply cannot be determined at all is
          &quot;unknown&quot;, never treated as if it disqualified the match. A dedicated{" "}
          <code>skill_relevance</code> criterion compares the applicant&apos;s own stated skills against the
          listing&apos;s own title/requirements/body text directly (a title-level match counting more than a
          body-level one, and a multi-word phrase more than a single generic word) - this is what actually
          tells a genuinely relevant listing apart from an unrelated one that merely shares the same broad
          field:
        </p>
        {weights ? (
          <table className="mt-2 text-sm text-gray-700">
            <tbody>
              {Object.entries(weights).map(([name, w]) => (
                <tr key={name}>
                  <td className="pr-4 font-mono">{name}</td>
                  <td>{w} / {Object.values(weights).reduce((a, b) => a + b, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-gray-500">{t("loading")}</p>
        )}
        <p className="mt-2 text-sm text-gray-700">
          This is a deterministic, documented heuristic, not a machine-learning ranking and not a guarantee
          of fit - every score is shown with its own per-criterion breakdown and reasons, never a bare
          number.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Thesis-formatting &amp; similarity module</h2>
        <p className="text-sm text-gray-700">
          A separate, optional module ({t("nav_thesis")}) checks a thesis document&apos;s own structural
          formatting against a template and produces a heuristic similarity ESTIMATE against open-access
          sources and any reference documents you upload yourself. The document is analysed entirely on this
          server and never sent anywhere else, unless you explicitly enable an external check yourself (each
          one is off by default and named individually before you run it). The similarity estimate is not a
          plagiarism finding and is never presented as equivalent to a commercial service such as
          Turnitin/iThenticate - see the {t("nav_thesis")} page&apos;s own report for the exact wording shown
          every time a check runs.
        </p>
      </section>
    </div>
  );
}
