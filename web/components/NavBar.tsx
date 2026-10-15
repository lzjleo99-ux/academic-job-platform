"use client";
import Link from "next/link";
import { useI18n } from "@/lib/i18n/context";
import { LocaleSwitcher } from "./LocaleSwitcher";

export function NavBar() {
  const { t } = useI18n();
  return (
    <header className="border-b border-gray-200 bg-white">
      <div className="mx-auto max-w-6xl px-4 py-3 flex items-center justify-between gap-4 flex-wrap">
        <Link href="/" className="font-semibold text-gray-900">
          Academic Job Platform
        </Link>
        <nav className="flex items-center gap-4 text-sm text-gray-700">
          <Link href="/" className="hover:underline">
            {t("nav_listings")}
          </Link>
          <Link href="/channels" className="hover:underline">
            {t("nav_channels")}
          </Link>
          <Link href="/orgs" className="hover:underline">
            {t("nav_orgs")}
          </Link>
          <Link href="/thesis" className="hover:underline">
            {t("nav_thesis")}
          </Link>
          <Link href="/applicants" className="hover:underline">
            {t("nav_applicants")}
          </Link>
          <Link href="/about/methodology" className="hover:underline">
            {t("nav_methodology")}
          </Link>
          <Link href="/compliance" className="hover:underline">
            {t("nav_compliance")}
          </Link>
          {/* T48: the classifier's own review queue. Public on purpose - "here is what our automated
              classification is unsure about" is a transparency statement, not an internal tool. */}
          <Link href="/review" className="hover:underline">
            {t("nav_review")}
          </Link>
        </nav>
        <LocaleSwitcher />
      </div>
    </header>
  );
}
