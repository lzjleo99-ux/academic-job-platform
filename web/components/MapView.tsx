"use client";
// Leaflet + OSM tiles, no API key. Client-only (Leaflet touches `window`/`document` at import time) - always
// loaded via `next/dynamic(() => import('./MapView'), { ssr: false })` from the page, never imported directly.
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { useEffect, useRef } from "react";
import Link from "next/link";
import { createRoot } from "react-dom/client";

// Leaflet's default marker icon paths assume a classic (non-bundled) asset layout; point them at the CDN
// copies instead of shipping broken relative paths through webpack.
const DEFAULT_ICON = L.icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41],
});

export interface MapPoint {
  id: string;
  title: string;
  lat: number;
  lon: number;
}

export function MapView({ points, height = 320 }: { points: MapPoint[]; height?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = L.map(ref.current, { scrollWheelZoom: false });
    mapRef.current = map;
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const markers: L.Marker[] = [];
    for (const p of points) {
      const marker = L.marker([p.lat, p.lon], { icon: DEFAULT_ICON }).addTo(map);
      const container = document.createElement("div");
      createRoot(container).render(
        <Link href={`/listing/${p.id}`} className="text-blue-700 hover:underline">
          {p.title}
        </Link>,
      );
      marker.bindPopup(container);
      markers.push(marker);
    }
    if (points.length > 0) {
      map.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lon])), { padding: [24, 24], maxZoom: 12 });
    } else {
      map.setView([48, 15], 4); // roughly centred on Europe when nothing has coordinates yet
    }
    return () => {
      markers.forEach((m) => m.remove());
    };
  }, [points]);

  return <div ref={ref} style={{ height }} className="w-full rounded-lg border border-gray-200" data-testid="leaflet-map" />;
}
