"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";
import type { ChannelLinkOut } from "@/lib/types";

export default function ChannelsPage() {
  const { t } = useI18n();
  const [channels, setChannels] = useState<ChannelLinkOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.channels().then(setChannels).catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <h1 className="text-2xl font-semibold text-gray-900">{t("nav_channels")}</h1>
      <p className="mt-2 text-sm text-gray-600">{t("channels_intro")}</p>

      {error && <p className="mt-4 text-red-600 text-sm">{t("error_loading")}</p>}
      {!channels && !error && <p className="mt-4 text-sm text-gray-500">{t("loading")}</p>}

      <ul className="mt-4 space-y-3" data-testid="channel-list">
        {(channels ?? []).map((c) => (
          <li key={`${c.source_id}-${c.slug}`} className="rounded-lg border border-gray-200 p-4">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h2 className="font-medium text-gray-900">{c.label}</h2>
                {c.description && <p className="mt-1 text-sm text-gray-600">{c.description}</p>}
              </div>
              <a
                href={c.url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="shrink-0 rounded bg-blue-600 text-white px-3 py-1.5 text-sm hover:bg-blue-700"
              >
                {t("channels_open")}
              </a>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
