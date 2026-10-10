import { Suspense } from "react";
import ListingsPage from "@/components/pages/ListingsPage";

// ListingsPage uses useSearchParams (the URL-synced filters) - Next.js requires that to sit inside a Suspense
// boundary so the rest of the shell can still be prerendered. See lib/useUrlParams.ts.
export default function Page() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-6xl px-4 py-6 text-sm text-gray-500">Loading...</div>}>
      <ListingsPage />
    </Suspense>
  );
}
