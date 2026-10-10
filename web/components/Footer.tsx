"use client";
import Link from "next/link";
import { useI18n } from "@/lib/i18n/context";

export function Footer() {
  const { t } = useI18n();
  return (
    <footer className="border-t border-gray-200 bg-white mt-8">
      <div className="mx-auto max-w-6xl px-4 py-4 flex items-center gap-4 text-xs text-gray-500 flex-wrap">
        <Link href="/about/methodology" className="hover:underline">
          {t("nav_methodology")}
        </Link>
        <Link href="/privacy" className="hover:underline">
          {t("nav_privacy")}
        </Link>
        <Link href="/terms" className="hover:underline">
          {t("nav_terms")}
        </Link>
        <Link href="/compliance" className="hover:underline">
          {t("nav_compliance")}
        </Link>
      </div>
    </footer>
  );
}
