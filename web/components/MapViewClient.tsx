"use client";
// next/dynamic's `ssr: false` is only allowed inside a Client Component (Leaflet touches `window` at import
// time, which would otherwise break the server-rendered pass of any page that includes it).
import dynamic from "next/dynamic";

export const MapViewClient = dynamic(() => import("./MapView").then((m) => m.MapView), {
  ssr: false,
  loading: () => <div className="w-full h-80 rounded-lg border border-gray-200 animate-pulse bg-gray-50" />,
});
