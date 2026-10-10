"use client";
import { useI18n } from "@/lib/i18n/context";

export default function PrivacyPage() {
  const { t } = useI18n();
  return (
    <div className="mx-auto max-w-3xl px-4 py-6 prose prose-sm">
      <h1 className="text-2xl font-semibold text-gray-900">{t("privacy_title")}</h1>
      <p className="text-sm text-gray-500">
        This page describes what this platform itself does, exactly as the code behind it actually behaves -
        not a generic template. If anything here ever stops matching the real behaviour, that is a bug in
        this page, not a change of policy.
      </p>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Job listing data</h2>
        <p className="text-sm text-gray-700">
          Job listings are collected from institutions&apos; own public employment boards, national
          employment services, and public academic-job aggregators - see the{" "}
          {t("nav_methodology")} page for the full list. Every field in a listing is what the original ad
          itself stated (kept with the exact wording it came from); nothing about a listing is personal data
          about you, the visitor.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Your browser: local storage only</h2>
        <p className="text-sm text-gray-700">
          The only thing this site stores in your browser is your chosen display language (English, Serbian
          Latin or Serbian Cyrillic), kept in your browser&apos;s own local storage so it&apos;s remembered
          next time you visit. It never leaves your browser and is never read by this platform&apos;s own
          servers. No analytics, tracking pixel, advertising cookie or third-party script runs on this site.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Thesis documents you upload</h2>
        <p className="text-sm text-gray-700">
          A thesis document you upload for formatting checks or a similarity estimate is processed entirely
          on this platform&apos;s own server; the file itself is never sent anywhere else. Its full text is
          also never sent anywhere else - the similarity check instead sends a small number of short probe
          phrases (a handful of the document&apos;s own longest sentences, never the full text) to whichever
          external sources you explicitly enable yourself before running the check; each one is listed by
          name and off by default. Copyleaks specifically is only ever contacted if you both have it
          configured and explicitly tick its own separate consent checkbox for that one run - never
          automatically, and never bundled into the general similarity check.
        </p>
        <p className="text-sm text-gray-700">
          <strong>Retention:</strong> an uploaded document and its analysis stay on this server until removed.
          A mechanism to purge documents older than a set number of days exists in the code, but as of this
          release it is not yet run automatically on any schedule - stated here plainly rather than claimed
          as done. A self-service &ldquo;{"Delete this document"}&rdquo; button is available on every
          document&apos;s own report page - it permanently erases the uploaded file itself, not just the
          report.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">CVs you upload for job matching</h2>
        <p className="text-sm text-gray-700">
          A CV you upload on the {t("nav_applicants")} page is processed entirely on this platform&apos;s own
          server - the file, its extracted text, and the profile built from it are never sent anywhere else,
          to any other service, for any reason. Nothing about matching uses an outbound request at all: every
          listing you are matched against already lives in this platform&apos;s own database, so no part of
          your CV or profile is ever transmitted to look up a match.
        </p>
        <p className="text-sm text-gray-700">
          <strong>Retention:</strong> your CV and profile stay on this server until you delete them yourself.
          The &ldquo;{"Delete my CV and profile"}&rdquo; button on the {t("nav_applicants")} page is a real,
          immediate delete - it erases the uploaded file and every piece of extracted text, keeping only a
          bare record that an applicant with that id once existed (so a match you had already computed can
          still be explained after the fact, without keeping anything else about you).
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">What we don&apos;t do</h2>
        <p className="text-sm text-gray-700">
          This platform has no user accounts, no login, and does not ask you for your name, email address or
          any other personal detail to browse listings or run a thesis check. It never sells or shares data
          with advertisers, because it collects none to sell.
        </p>
      </section>

      <section className="mt-4">
        <h2 className="text-lg font-medium text-gray-900">Contact</h2>
        <p className="text-sm text-gray-700">
          Questions about this page, a request to remove an uploaded thesis document, or anything else: use
          the contact address shown in this platform&apos;s own outbound requests (its identifying User-Agent
          header), configured by whoever operates this instance.
        </p>
      </section>
    </div>
  );
}
