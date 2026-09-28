import { useEffect } from "react";
import { CircleMarker, GeoJSON, MapContainer, Marker, Polygon, Polyline, TileLayer, Tooltip, useMap } from "react-leaflet";
import type { LatLngExpression } from "leaflet";
import L from "leaflet";

export interface LayerData {
  bounds: { min_lon: number; max_lon: number; min_lat: number; max_lat: number };
  manholes: any; sewerlines: any; nala: any; roads: any; historical_flood: any;
  cctv_cameras: { camera_id: string; lat: number; lon: number; road_id: string; location: string; video_url: string }[];
}
const roadColor = (d: any) => d.status === "CLOSED" ? "#dc2626" : d.status === "RESTRICTED" ? "#f97316" : "#55667c";
const nodeColor = (risk: string) => risk === "CRITICAL" ? "#dc2626" : risk === "HIGH" ? "#f97316" : risk === "WARNING" ? "#facc15" : "#22c55e";
const cameraColor = (state: string) => state === "NORMAL" ? "#22c55e" : state === "LIGHT WATERLOGGING" ? "#eab308" : state === "WATERLOGGING" ? "#f97316" : "#dc2626";
const floodColor = (risk: string) => risk === "SEVERE" ? "#dc2626" : risk === "HIGH" ? "#f97316" : risk === "MEDIUM" ? "#eab308" : "#38bdf8";

function Fit({ bounds }: { bounds: LayerData["bounds"] | null }) {
  const map = useMap();
  useEffect(() => { if (bounds) map.fitBounds([[bounds.min_lat, bounds.min_lon], [bounds.max_lat, bounds.max_lon]], { padding: [28, 28] }); }, [bounds, map]);
  return null;
}
function Clicker({ enabled, onClick }: { enabled: boolean; onClick: (lat: number, lon: number) => void }) {
  const map = useMap();
  useEffect(() => {
    if (!enabled) return;
    const cb = (e: any) => onClick(e.latlng.lat, e.latlng.lng);
    map.on("click", cb);
    return () => { map.off("click", cb); };
  }, [map, enabled, onClick]);
  return null;
}
const pin = new L.DivIcon({ className: "route-pin", html: "<span></span>", iconSize: [18, 18], iconAnchor: [9, 9] });

export default function MapView({ layers, simulation, visibleLayers, route, routeClicks, selectionMode, onMapClick, onSelect }: any) {
  const roadLookup = new Map(simulation.roads.map((r: any) => [r.road_id, r]));
  return <MapContainer center={[17.375, 78.47]} zoom={15} style={{ height: "100%", width: "100%" }}>
    <TileLayer attribution="&copy; OpenStreetMap" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
    <Fit bounds={layers?.bounds || null} /><Clicker enabled={selectionMode} onClick={onMapClick} />
    {layers && visibleLayers.flood && simulation.polygons.map((p: any) => <Polygon key={p.id} positions={p.coordinates.map(([lon, lat]: number[]) => [lat, lon])} pathOptions={{ color: floodColor(p.risk), fillColor: floodColor(p.risk), fillOpacity: .24, weight: 1.5 }} eventHandlers={{ click: () => onSelect("hotspot", p) }}><Tooltip sticky>{p.id} · {p.depth} cm</Tooltip></Polygon>)}
    {layers && visibleLayers.drainage && <><GeoJSON data={layers.sewerlines} style={() => ({ color: "#06b6d4", weight: 2, opacity: .72, dashArray: "5 4" })} /><GeoJSON data={layers.nala} style={() => ({ color: "#0284c7", weight: 5, opacity: .82 })} /></>}
    {layers && visibleLayers.roads && <GeoJSON data={layers.roads} style={(f: any) => { const d = roadLookup.get(f.properties.road_id) || f.properties; return { color: roadColor(d), weight: d.width > 12 ? 7 : d.width > 7 ? 4 : 2.3, opacity: .94, dashArray: d.status === "CLOSED" ? "6 5" : undefined }; }} onEachFeature={(f: any, layer: any) => { const d = roadLookup.get(f.properties.road_id) || f.properties; layer.bindTooltip(`${d.road_id} · ${d.depth || 0} cm`, { sticky: true }); layer.on("click", () => onSelect("road", d)); }} />}
    {layers && visibleLayers.nodes && layers.manholes.features.map((f: any) => { const d = simulation.nodes.find((n: any) => n.node_id === f.properties.node_id) || f.properties; const [lon, lat] = f.geometry.coordinates; return <CircleMarker key={d.node_id} center={[lat, lon]} radius={d.risk === "CRITICAL" ? 7 : 5} pathOptions={{ color: "#fff", weight: 1, fillColor: nodeColor(d.risk), fillOpacity: 1 }} eventHandlers={{ click: () => onSelect("node", d) }}><Tooltip>{d.node_id} · {d.utilization}% utilization</Tooltip></CircleMarker>; })}
    {layers && visibleLayers.cctv && simulation.cameras.map((d: any) => <CircleMarker key={d.camera_id} center={[d.lat, d.lon]} radius={8} pathOptions={{ color: "#fff", weight: 2, fillColor: cameraColor(d.status), fillOpacity: 1 }} eventHandlers={{ click: () => onSelect("camera", d) }}><Tooltip>{d.camera_id} · {d.status}</Tooltip></CircleMarker>)}
    {routeClicks.map((p: any, i: number) => <Marker key={i} position={[p.lat, p.lon]} icon={pin}><Tooltip permanent direction="top">{i === 0 ? "START" : "DESTINATION"}</Tooltip></Marker>)}
    {route?.normal_route?.path && <Polyline positions={route.normal_route.path.map(([lon, lat]: number[]) => [lat, lon] as LatLngExpression)} pathOptions={{ color: "#64748b", weight: 4, dashArray: "7 7" }} />}
    {route?.flood_aware_route?.path && <Polyline positions={route.flood_aware_route.path.map(([lon, lat]: number[]) => [lat, lon] as LatLngExpression)} pathOptions={{ color: "#16a34a", weight: 5 }} />}
  </MapContainer>;
}
