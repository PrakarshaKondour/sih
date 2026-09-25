import { useEffect, useMemo, useRef } from "react";
import {
  MapContainer,
  TileLayer,
  GeoJSON,
  CircleMarker,
  Popup,
  useMap,
} from "react-leaflet";
import type { LatLngExpression } from "leaflet";

export interface LayerData {
  bounds: { min_lon: number; max_lon: number; min_lat: number; max_lat: number };
  manholes: GeoJSON.FeatureCollection;
  sewerlines: any;
  nala: any;
  roads: any;
  historical_flood: any;
  cctv_cameras: { camera_id: string; lat: number; lon: number }[];
}

export interface NowcastEntry {
  node_id: string;
  forecast: { minute: number; predicted_depth_m: number; risk: string }[];
  expected_onset_minute: number | null;
}

const RISK_COLOR: Record<string, string> = {
  NONE: "#3b4a63",
  LOW: "#4ade80",
  MODERATE: "#facc15",
  HIGH: "#fb923c",
  CRITICAL: "#ef4444",
};

function sourceColor(source: string | undefined): string {
  if (!source) return "#888";
  if (source.startsWith("real")) return "#38bdf8"; // blue = verified real
  if (source.startsWith("inferred")) return "#a78bfa"; // purple = DEM-inferred
  return "#fb923c"; // orange = synthetic-demo
}

function FitBounds({ bounds }: { bounds: LayerData["bounds"] | null }) {
  const map = useMap();
  useEffect(() => {
    if (!bounds) return;
    map.fitBounds([
      [bounds.min_lat, bounds.min_lon],
      [bounds.max_lat, bounds.max_lon],
    ]);
  }, [bounds, map]);
  return null;
}

export default function MapView({
  layers,
  nowcast,
  minute,
  visibleLayers,
  route,
  onMapClick,
}: {
  layers: LayerData | null;
  nowcast: NowcastEntry[] | null;
  minute: number;
  visibleLayers: Record<string, boolean>;
  route: { normal: LatLngExpression[]; aware: LatLngExpression[] } | null;
  onMapClick: (lat: number, lon: number) => void;
}) {
  const center: LatLngExpression = [17.375, 78.47];
  const nowcastByNode = useMemo(() => {
    const m: Record<string, NowcastEntry> = {};
    (nowcast || []).forEach((n) => (m[n.node_id] = n));
    return m;
  }, [nowcast]);

  const ClickHandler = () => {
    const map = useMap();
    const handlerRef = useRef<any>(null);
    useEffect(() => {
      const handler = (e: any) => onMapClick(e.latlng.lat, e.latlng.lng);
      map.on("click", handler);
      handlerRef.current = handler;
      return () => {
        map.off("click", handler);
      };
    }, [map]);
    return null;
  };

  return (
    <MapContainer center={center} zoom={15} style={{ height: "100%", width: "100%" }}>
      <TileLayer
        attribution='&copy; OpenStreetMap contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {layers && <FitBounds bounds={layers.bounds} />}
      <ClickHandler />

      {layers && visibleLayers.roads && (
        <GeoJSON
          data={layers.roads}
          style={() => ({ color: "#5b6b85", weight: 2, opacity: 0.6 })}
        />
      )}

      {layers && visibleLayers.sewerlines && (
        <GeoJSON
          data={layers.sewerlines}
          style={(f: any) => ({
            color: sourceColor(f?.properties?.source),
            weight: 2,
            dashArray: f?.properties?.source?.startsWith("real") ? undefined : "4 3",
          })}
        />
      )}

      {layers && visibleLayers.nala && (
        <GeoJSON
          data={layers.nala}
          style={(f: any) => ({
            color: sourceColor(f?.properties?.source),
            weight: 4,
            opacity: 0.8,
          })}
        />
      )}

      {layers && visibleLayers.historical_flood && (
        <GeoJSON
          data={layers.historical_flood}
          style={() => ({ color: "#ef4444", fillColor: "#ef4444", fillOpacity: 0.15, weight: 1, dashArray: "3 3" })}
        />
      )}

      {layers &&
        visibleLayers.manholes &&
        layers.manholes.features.map((f: any, i: number) => {
          const [lon, lat] = f.geometry.coordinates;
          const nodeId = f.properties.node_id;
          const nc = nowcastByNode[nodeId];
          const forecastNow = nc?.forecast.find((x) => x.minute === minute);
          const risk = forecastNow?.risk || "NONE";
          const color = visibleLayers.nowcast ? RISK_COLOR[risk] : sourceColor(f.properties.source);
          return (
            <CircleMarker
              key={i}
              center={[lat, lon]}
              radius={visibleLayers.nowcast && risk !== "NONE" ? 6 : 3}
              pathOptions={{ color, fillColor: color, fillOpacity: 0.85, weight: 1 }}
            >
              <Popup>
                <div className="node-popup">
                  <div><b>{nodeId}</b></div>
                  <div>source: {f.properties.source} (confidence {f.properties.confidence})</div>
                  {forecastNow && (
                    <div style={{ marginTop: 4 }}>
                      depth @ T+{minute}min: <b>{forecastNow.predicted_depth_m}m</b>{" "}
                      <span className={`risk-pill risk-${risk}`}>{risk}</span>
                    </div>
                  )}
                </div>
              </Popup>
            </CircleMarker>
          );
        })}

      {layers &&
        visibleLayers.cctv &&
        layers.cctv_cameras.map((cam, i) => (
          <CircleMarker
            key={`cam-${i}`}
            center={[cam.lat, cam.lon]}
            radius={7}
            pathOptions={{ color: "#22d3ee", fillColor: "#22d3ee", fillOpacity: 0.9, weight: 2 }}
          >
            <Popup>CCTV camera: {cam.camera_id} (synthetic demo frame)</Popup>
          </CircleMarker>
        ))}

      {route && (
        <>
          <GeoJSON
            data={{
              type: "Feature",
              geometry: { type: "LineString", coordinates: route.normal.map((p: any) => [p[1], p[0]]) },
              properties: {},
            } as any}
            style={() => ({ color: "#8ea0bd", weight: 3, dashArray: "5 5" })}
          />
          <GeoJSON
            data={{
              type: "Feature",
              geometry: { type: "LineString", coordinates: route.aware.map((p: any) => [p[1], p[0]]) },
              properties: {},
            } as any}
            style={() => ({ color: "#22d3ee", weight: 4 })}
          />
        </>
      )}
    </MapContainer>
  );
}
