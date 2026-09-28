import { useEffect, useMemo, useState } from "react";
import MapView, { LayerData } from "./components/MapView";
import { computeRoute, getLayers } from "./api";

const MINUTES = [0, 15, 30, 60, 120, 180];
const depthRisk = (d: number) => d > 50 ? "SEVERE" : d > 30 ? "HIGH" : d > 15 ? "MEDIUM" : "LOW";
const cameraStatus = (rain: number, i: number) => (rain >= 100
  ? ["LIGHT WATERLOGGING", "WATERLOGGING", "SEVERE FLOOD", "SEVERE FLOOD", "BLOCKAGE"]
  : rain >= 72 ? ["NORMAL", "LIGHT WATERLOGGING", "WATERLOGGING", "SEVERE FLOOD", "BLOCKAGE"]
  : ["NORMAL", "NORMAL", "LIGHT WATERLOGGING", "NORMAL", "NORMAL"])[i];

export default function App() {
  const [layers, setLayers] = useState<LayerData | null>(null);
  const [rainfall, setRainfall] = useState(72);
  const [minute, setMinute] = useState(60);
  const [autoSim, setAutoSim] = useState(false);
  const [visible, setVisible] = useState<Record<string, boolean>>({ roads: true, drainage: true, nodes: true, cctv: true, flood: true, hotspots: true });
  const [selected, setSelected] = useState<any>(null);
  const [routeClicks, setRouteClicks] = useState<{ lat: number; lon: number }[]>([]);
  const [route, setRoute] = useState<any>(null);
  const [profile, setProfile] = useState("NORMAL");
  const [routing, setRouting] = useState(false);
  const [selectingRoute, setSelectingRoute] = useState(false);

  useEffect(() => { getLayers().then(setLayers).catch(() => undefined); }, []);
  useEffect(() => {
    if (!autoSim) return;
    const timer = window.setInterval(() => setRainfall((r) => r >= 112 ? 42 : r + 7), 3000);
    return () => window.clearInterval(timer);
  }, [autoSim]);

  const simulation = useMemo(() => {
    const storm = Math.max(0, (rainfall - 28) / 84);
    const tf = 0.7 + minute / 260;
    const roads = (layers?.roads?.features || []).map((f: any, i: number) => {
      const depth = Math.round(Math.max(0, (storm * 50 + [4, 10, 16, 25, 7][i % 5] - 11) * tf));
      return { ...f.properties, depth, risk: depthRisk(depth), status: depth > 50 ? "CLOSED" : depth > 30 ? "RESTRICTED" : "OPEN" };
    });
    const nodes = (layers?.manholes?.features || []).map((f: any, i: number) => {
      const utilization = Math.min(99, Math.round(35 + storm * 58 + (i % 4) * 4));
      return { ...f.properties, utilization, water: Math.round(storm * 48 + (i % 5) * 3), risk: utilization > 90 ? "CRITICAL" : utilization > 76 ? "HIGH" : utilization > 58 ? "WARNING" : "NORMAL" };
    });
    const cameras = (layers?.cctv_cameras || []).map((c: any, i: number) => ({ ...c, status: cameraStatus(rainfall, i), depth: Math.round(Math.max(0, storm * (18 + i * 10))), confidence: 88 + (i % 4) * 3 }));
    const b = layers?.bounds;
    const centers = [[.27,.53],[.48,.46],[.66,.57],[.79,.43]];
    const polygons = b ? centers.map(([x, y], i) => {
      const radius = .0006 + storm * (.001 + i * .00018);
      const coordinates = Array.from({ length: 11 }, (_, j) => {
        const angle = j / 10 * Math.PI * 2, wobble = .74 + ((j * 17 + i * 11) % 29) / 100;
        return [b.min_lon + x * (b.max_lon - b.min_lon) + Math.cos(angle) * radius * wobble, b.min_lat + y * (b.max_lat - b.min_lat) + Math.sin(angle) * radius * wobble];
      });
      const depth = Math.round(12 + storm * (25 + i * 12));
      return { id: `HS-${String(i + 1).padStart(3, "0")}`, coordinates, depth, risk: depthRisk(depth), roadId: `R-${String([10,17,27,36][i]).padStart(3, "0")}`, nodeId: `DN-${String(i + 7).padStart(3, "0")}`, cameraId: `CAM-${String(i + 2).padStart(3, "0")}` };
    }) : [];
    return { roads, nodes, cameras, polygons, alerts: roads.filter((r: any) => r.status !== "OPEN").slice(0, 4) };
  }, [layers, rainfall, minute]);

  const calculateRoute = async () => {
    if (routeClicks.length !== 2) return;
    setRouting(true);
    try { setRoute(await computeRoute({ start_lon: routeClicks[0].lon, start_lat: routeClicks[0].lat, end_lon: routeClicks[1].lon, end_lat: routeClicks[1].lat, profile: profile as any, horizon_minute: minute })); }
    finally { setRouting(false); }
  };
  const onMapClick = (lat: number, lon: number) => {
    if (!selectingRoute) return;
    const point = { lat, lon };
    setRouteClicks((points) => {
      if (points.length === 0) return [point];
      setSelectingRoute(false);
      return [points[0], point];
    });
  };
  const toggleMapSelection = () => {
    if (!selectingRoute) {
      setRouteClicks([]);
      setRoute(null);
    }
    setSelectingRoute((active) => !active);
  };
  const pipeline = ["Rainfall Nowcast", "Surface Flow", "Drainage Graph", "Flood Nowcast", "CCTV Analysis", "Routing & Emergency APIs", "Public Alerts"];

  return <div className="app">
    <header className="header"><div><strong>HYDROLOOP</strong><span>Synthetic urban flood nowcasting</span></div><div className="live-chip"><i /> LIVE SYNTHETIC T+{minute}</div></header>
    <aside className="sidebar">
      <section className="section"><div className="section-title">Storm control</div><div className="rain-readout"><strong>{rainfall}</strong><span>mm/hr rainfall</span><b>{rainfall >= 100 ? "EXTREME" : rainfall >= 72 ? "HEAVY" : "MODERATE"}</b></div><input aria-label="Rainfall intensity" type="range" min="35" max="115" value={rainfall} onChange={(e) => setRainfall(Number(e.target.value))} /><label className="toggle-row"><span>Auto simulation</span><input type="checkbox" checked={autoSim} onChange={(e) => setAutoSim(e.target.checked)} /></label></section>
      <section className="section"><div className="section-title">Flood-aware routing</div><p className="muted">{selectingRoute ? (routeClicks.length ? "Select your destination on the map." : "Select your source on the map.") : "Choose both locations from the map."}</p><button className={`map-select-button ${selectingRoute ? "active" : ""}`} onClick={toggleMapSelection}>{selectingRoute ? "Picking locations..." : "Select on Map"}</button><div className="route-point"><b>Source / Start</b><span>{routeClicks[0] ? `${routeClicks[0].lat.toFixed(5)}, ${routeClicks[0].lon.toFixed(5)}` : "Not selected"}</span></div><div className="route-point"><b>Destination / End</b><span>{routeClicks[1] ? `${routeClicks[1].lat.toFixed(5)}, ${routeClicks[1].lon.toFixed(5)}` : "Not selected"}</span></div><select value={profile} onChange={(e) => setProfile(e.target.value)}><option value="NORMAL">Normal vehicle</option><option value="AMBULANCE">Ambulance</option><option value="FIRE">Fire response</option></select><button className="primary-button" disabled={routeClicks.length !== 2 || routing} onClick={calculateRoute}>{routing ? "Checking network..." : "Calculate safe route"}</button>{route && <div className="route-summary"><strong>{route.reroute_required ? "Alternate route selected" : "Route assessed"}</strong><span>Source node {route.selected_start?.node || "mapped"}</span><span>Destination node {route.selected_end?.node || "mapped"}</span><span>Original {(route.normal_route.length_m / 1000).toFixed(1)} km · Safe {(route.flood_aware_route.length_m / 1000).toFixed(1)} km</span><small>{route.blocked_road_ids?.length || 0} flooded road(s) avoided</small></div>}</section>
      <section className="section"><div className="section-title">Map layers</div>{Object.keys(visible).map((name) => <label className="toggle-row" key={name}><span>{name}</span><input type="checkbox" checked={visible[name]} onChange={(e) => setVisible((v) => ({ ...v, [name]: e.target.checked }))} /></label>)}</section>
      <section className="section"><div className="section-title">Active alerts</div>{simulation.alerts.length ? simulation.alerts.map((r: any) => <div className="alert-row" key={r.road_id}><b>{r.road_id}</b><span>{r.depth} cm · {r.status}</span></div>) : <p className="muted">No road restrictions.</p>}</section>
    </aside>
    <main className={`map-wrap ${selectingRoute ? "selecting-route" : ""}`}><MapView layers={layers} simulation={simulation} minute={minute} visibleLayers={visible} route={route} routeClicks={routeClicks} selectionMode={selectingRoute} onMapClick={onMapClick} onSelect={(kind: string, data: any) => setSelected({ kind, data })} /></main>
    <footer className="timeline"><div className="timeline-head"><span>Now</span><strong>Forecast horizon T+{minute} min</strong><span>T+180</span></div><input type="range" min="0" max={MINUTES.length - 1} value={MINUTES.indexOf(minute)} onChange={(e) => setMinute(MINUTES[Number(e.target.value)])} /><div className="legend"><span><i className="low" />Low 0-15 cm</span><span><i className="medium" />Medium 15-30 cm</span><span><i className="high" />High 30-50 cm</span><span><i className="severe" />Severe &gt;50 cm</span><span><i className="drain" />Drainage</span><span><i className="safe" />Alternate route</span></div></footer>
    <div className="architecture"><div className="architecture-label">System pipeline</div>{pipeline.map((name) => <button key={name} onClick={() => setSelected({ kind: "architecture", data: { name } })}>{name}</button>)}</div>
    {selected && <InfoPanel selected={selected} onClose={() => setSelected(null)} rainfall={rainfall} />}
  </div>;
}

function InfoPanel({ selected, onClose, rainfall }: { selected: any; onClose: () => void; rainfall: number }) {
  const d = selected.data;
  const canPlayCamera = ["WATERLOGGING", "SEVERE FLOOD", "BLOCKAGE"].includes(d.status);
  const rows: [string, any][] = selected.kind === "road" ? [["Name", d.road_name], ["Type", d.road_class], ["Length", `${(d.length / 1000).toFixed(2)} km`], ["Width / speed", `${d.width} m / ${d.speedLimit} km/h`], ["Flood depth", `${d.depth} cm (${d.risk})`], ["Drainage nodes", d.drainageNodeIds?.join(", ")], ["CCTV cameras", d.cctvIds?.join(", ") || "None"]]
    : selected.kind === "node" ? [["Type / location", `Manhole · ${d.label}`], ["Connected roads", d.connected_roads?.join(", ")], ["Current flow", `${d.utilization - 12}%`], ["Capacity utilization", `${d.utilization}%`], ["Water level", `${d.water} cm`], ["Blockage probability", `${d.blockage}%`], ["Flow direction", `${d.upstream?.slice(0,2).join(", ") || "Catchment inlet"} → ${d.downstream}`]]
    : selected.kind === "hotspot" ? [["Flood depth", `${d.depth} cm`], ["Affected road", d.roadId], ["Nearest drainage", d.nodeId], ["CCTV confirmation", d.cameraId], ["Estimated duration", `${Math.round(40 + rainfall * .7)} min`], ["Cause", "Drainage capacity exceeded"]]
    : [["Data source", "Synthetic demo catchment"], ["System state", "Operational"], ["Current rainfall", `${rainfall} mm/hr`]];
  return <div className="info-panel"><button className="close-button" onClick={onClose}>×</button>{selected.kind === "camera" ? <><div className="eyebrow">CCTV observation</div><h2>{d.camera_id}</h2><div className="status-badge">{d.status}</div><div className="video-shell">{canPlayCamera ? <><video key={d.video_url} controls autoPlay muted playsInline preload="metadata" src={d.video_url} /><div>{d.camera_id}<br />SYNTHETIC CCTV FEED<br />{d.status} DETECTED</div></> : <div className="camera-paused">Camera feed paused—No active flood risk.</div>}</div><dl><dt>Location</dt><dd>{d.location}</dd><dt>Road</dt><dd>{d.road_id}</dd><dt>Estimated depth</dt><dd>{d.depth} cm</dd><dt>Detection confidence</dt><dd>{d.confidence}%</dd><dt>Timestamp</dt><dd>{new Date().toLocaleTimeString()}</dd></dl></> : <><div className="eyebrow">{selected.kind === "architecture" ? "System component" : selected.kind === "node" ? "Drainage node" : selected.kind === "hotspot" ? "Flood hotspot" : "Road information"}</div><h2>{selected.kind === "architecture" ? d.name : selected.kind === "hotspot" ? d.id : selected.kind === "node" ? d.node_id : d.road_id}</h2>{d.status || d.risk ? <div className="status-badge">{d.status || d.risk}</div> : null}<dl>{rows.map(([label, value]) => <><dt key={label + "dt"}>{label}</dt><dd key={label + "dd"}>{value}</dd></>)}</dl></>}</div>;
}
