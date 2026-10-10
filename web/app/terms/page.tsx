"use client";
import { useI18n } from "@/lib/i18n/context";

export default function TermsPage() {
  const { t } = useI18n();
  return (
    <div className="mx-auto max-w-3xl px-4 py-6 prose prose-sm">
      <h1 className="text-2xl font-semibold text-gray-900">{t("terms_title")}</h1>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">What this platform is</h2>
        <p className="text-sm text-gray-700">
          This is a non-commercial aggregator of publicly available academic job listings. It does not
          publish original job advertisements of its own - every listing shown here originated on, and
          remains the property of, the institution or site that first published it. This platform only
          indexes and links to that original publication; using this site to find a listing does not create
          any relationship between you and this platform regarding that job or its employer.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Original sources and removal</h2>
        <p className="text-sm text-gray-700">
          Every listing links back to where it was actually published (or, for a source shown only as{" "}
          {t("nav_channels")}, opens a search on that site directly). Applying for a position always happens
          on the original source&apos;s own site, never on this platform. If you are a listing&apos;s
          original publisher and want it removed from this index, contact us using the address on the{" "}
          {t("nav_privacy")} page and it will be taken down.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Trademarks and names</h2>
        <p className="text-sm text-gray-700">
          Institution names, source names (e.g. jobs.ac.uk, Nature Careers, EURAXESS) and any other
          third-party names or logos that may appear on this site remain the property of their respective
          owners. Their appearance here is solely to identify where a listing came from and implies no
          endorsement, partnership or affiliation with this platform unless explicitly stated.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">No warranty</h2>
        <p className="text-sm text-gray-700">
          Listing data is aggregated automatically and may be incomplete, delayed or, occasionally, wrong -
          see the {t("nav_methodology")} page for exactly how each field is derived and what is left blank
          rather than guessed. Always verify a listing&apos;s current details (especially the deadline) on
          its original source before relying on them. This platform is provided &ldquo;as is&rdquo;, without
          warranty of any kind.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Thesis-formatting &amp; similarity module</h2>
        <p className="text-sm text-gray-700">
          The similarity estimate produced by the {t("nav_thesis")} module is a heuristic, not a plagiarism
          finding, and is not equivalent to a commercial service such as Turnitin/iThenticate - see that
          module&apos;s own report for the exact disclaimer shown every time a check runs, and the{" "}
          {t("nav_privacy")} page for what is and isn&apos;t sent anywhere when you use it.
        </p>
      </section>
    </div>
  );
}
