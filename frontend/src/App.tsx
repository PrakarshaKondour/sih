import { useEffect, useState } from "react";
import MapView, { LayerData } from "./components/MapView";
import { computeRoute, getDataStatus, getLayers, runScenario } from "./api";

const MINUTES = [0, 15, 30, 60, 120, 180];

export default function App() {
  const [layers, setLayers] = useState<LayerData | null>(null);
  const [scenario, setScenario] = useState<any>(null);
  const [rainfallMode, setRainfallMode] = useState<"real" | "demo">("demo");
  const [realRainAvailable, setRealRainAvailable] = useState(false);
  const [scenarioError, setScenarioError] = useState("");
  const [loadingScenario, setLoadingScenario] = useState(false);
  const [minute, setMinute] = useState(60);
  const [visible, setVisible] = useState<Record<string, boolean>>({ roads: true, drainage: true, inferred: true, nodes: true, cctv: true, flood: true });
  const [selected, setSelected] = useState<any>(null);
  const [routeClicks, setRouteClicks] = useState<{ lat: number; lon: number }[]>([]);
  const [route, setRoute] = useState<any>(null);
  const [profile, setProfile] = useState("NORMAL");
  const [routing, setRouting] = useState(false);
  const [selectingRoute, setSelectingRoute] = useState(false);

  useEffect(() => {
    getLayers().then(setLayers).catch(() => undefined);
    getDataStatus().then((status) => setRealRainAvailable(
      status.imerg_tiffs?.count > 0 || status.rainfall_csv?.available
    )).catch(() => undefined);
  }, []);

  useEffect(() => {
    let active = true;
    setLoadingScenario(true);
    setScenarioError("");
    runScenario(rainfallMode)
      .then((result) => { if (active) setScenario(result); })
      .catch((error) => { if (active) setScenarioError(error.message); })
      .finally(() => { if (active) setLoadingScenario(false); });
    return () => { active = false; };
  }, [rainfallMode]);

  const horizon = scenario?.horizons?.find((item: any) => item.minute === minute);
  const simulation = {
    roads: horizon?.roads || [],
    nodes: (scenario?.nowcast || []).map((entry: any) => {
      const forecast = entry.forecast.find((item: any) => item.minute === minute);
      return { ...entry, ...(forecast || {}), utilization: forecast?.capacity_utilization_pct ?? 0 };
    }),
    cameras: scenario?.cctv_cameras || [],
    hotspots: horizon?.hotspots || [],
    alerts: scenario?.alerts || [],
  };
  const rainfallTotal = (scenario?.rainfall_event || []).reduce(
    (sum: number, entry: any) => sum + Number(entry.rainfall_mm || 0), 0
  );
  const sourceLabel = scenario?.rainfall_source === "real:gpm-imerg" ? "REAL · IMERG"
    : scenario?.rainfall_source === "real:imd" ? "REAL · IMD" : "SYNTHETIC DEMO";

  const calculateRoute = async () => {
    if (routeClicks.length !== 2) return;
    setRouting(true);
    try { setRoute(await computeRoute({ start_lon: routeClicks[0].lon, start_lat: routeClicks[0].lat, end_lon: routeClicks[1].lon, end_lat: routeClicks[1].lat, profile: profile as any, horizon_minute: minute, rainfall_mode: rainfallMode })); }
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
  const pipeline = ["Rainfall input", "Runoff & flood model", "Drainage risk", "CCTV verification", "Road risk & routing", "Bilingual alerts"];

  return <div className="app">
    <header className="header"><div><strong>HYDROLOOP</strong><span>Hyderabad · catchment prototype</span></div><div className={`live-chip ${scenario?.data_source === "REAL" ? "real" : "demo"}`}><i /> {sourceLabel} · T+{minute}</div></header>
    <aside className="sidebar">
      <section className="section"><div className="section-title">Rainfall input</div><label className="field-label" htmlFor="rainfall-mode">Model input source</label><select id="rainfall-mode" value={rainfallMode} onChange={(e) => setRainfallMode(e.target.value as "real" | "demo")}><option value="demo">Demo rainfall · synthetic</option><option value="real" disabled={!realRainAvailable}>Real observations · IMERG / IMD{!realRainAvailable ? " unavailable" : ""}</option></select><div className="rain-readout"><strong>{rainfallTotal.toFixed(1)}</strong><span>mm event total</span><b>{loadingScenario ? "RUNNING" : scenario?.rainfall_source?.split(":")[1]?.toUpperCase() || "WAITING"}</b></div>{scenarioError && <p className="error-text">{scenarioError}</p>}<p className="muted">{scenario?.note || "Loading backend forecast…"}</p></section>
      <section className="section"><div className="section-title">Flood-aware routing</div><p className="muted">{selectingRoute ? (routeClicks.length ? "Select your destination on the map." : "Select your source on the map.") : "Choose both locations from the map."}</p><button className={`map-select-button ${selectingRoute ? "active" : ""}`} onClick={toggleMapSelection}>{selectingRoute ? "Picking locations..." : "Select on Map"}</button><div className="route-point"><b>Source / Start</b><span>{routeClicks[0] ? `${routeClicks[0].lat.toFixed(5)}, ${routeClicks[0].lon.toFixed(5)}` : "Not selected"}</span></div><div className="route-point"><b>Destination / End</b><span>{routeClicks[1] ? `${routeClicks[1].lat.toFixed(5)}, ${routeClicks[1].lon.toFixed(5)}` : "Not selected"}</span></div><select value={profile} onChange={(e) => setProfile(e.target.value)}><option value="NORMAL">Normal vehicle</option><option value="AMBULANCE">Ambulance</option><option value="FIRE">Fire response</option></select><button className="primary-button" disabled={routeClicks.length !== 2 || routing} onClick={calculateRoute}>{routing ? "Checking network..." : "Calculate safe route"}</button>{route && <div className="route-summary"><strong>{route.reroute_required ? "Alternate route selected" : "Route assessed"}</strong><span>Source node {route.selected_start?.node || "mapped"}</span><span>Destination node {route.selected_end?.node || "mapped"}</span><span>Original {(route.normal_route.length_m / 1000).toFixed(1)} km · Safe {(route.flood_aware_route.length_m / 1000).toFixed(1)} km</span><small>{route.blocked_road_ids?.length || 0} flooded road(s) avoided</small></div>}</section>
      <section className="section"><div className="section-title">Map layers</div>{Object.keys(visible).map((name) => <label className="toggle-row" key={name}><span>{name === "inferred" ? "inferred drainage" : name}</span><input type="checkbox" checked={visible[name]} onChange={(e) => setVisible((v) => ({ ...v, [name]: e.target.checked }))} /></label>)}<p className="muted">Roads: {layers?.sources?.roads || "loading"}<br />Drainage: {layers?.sources?.drainage || "loading"}<br />CCTV: synthetic samples</p></section>
      <section className="section"><div className="section-title">Flood alerts</div>{simulation.alerts.length ? simulation.alerts.map((alert: any) => <div className="alert-row" key={`${alert.minute}-${alert.severity}`}><b>{alert.severity} · T+{alert.minute}</b><span>{alert.messages.en}<br />{alert.messages.te}</span><small>{alert.dispatch.mode}</small></div>) : <p className="muted">No backend threshold has been crossed.</p>}</section>
    </aside>
    <main className={`map-wrap ${selectingRoute ? "selecting-route" : ""}`}><MapView layers={layers} simulation={simulation} visibleLayers={visible} route={route} routeClicks={routeClicks} selectionMode={selectingRoute} onMapClick={onMapClick} onSelect={(kind: string, data: any) => setSelected({ kind, data })} /></main>
    <footer className="timeline"><div className="timeline-head"><span>Now</span><strong>Forecast horizon T+{minute} min</strong><span>T+180</span></div><input type="range" min="0" max={MINUTES.length - 1} value={MINUTES.indexOf(minute)} onChange={(e) => setMinute(MINUTES[Number(e.target.value)])} /><div className="legend"><span><i className="low" />Low 0-15 cm</span><span><i className="medium" />Medium 15-30 cm</span><span><i className="high" />High 30-50 cm</span><span><i className="severe" />Severe &gt;50 cm</span><span><i className="drain" />Drainage</span><span><i className="safe" />Alternate route</span></div></footer>
    <div className="architecture"><div className="architecture-label">System pipeline</div>{pipeline.map((name) => <button key={name} onClick={() => setSelected({ kind: "architecture", data: { name } })}>{name}</button>)}</div>
    {selected && <InfoPanel selected={selected} onClose={() => setSelected(null)} rainfall={rainfallTotal} />}
  </div>;
}

function InfoPanel({ selected, onClose, rainfall }: { selected: any; onClose: () => void; rainfall: number }) {
  const d = selected.data;
  const canPlayCamera = ["WATERLOGGING", "SEVERE FLOOD", "BLOCKAGE"].includes(d.status);
  const rows: [string, any][] = selected.kind === "road" ? [["Name", d.road_name || "Unnamed road"], ["Type", d.road_class], ["Flood depth", `${(d.depth_m * 100).toFixed(1)} cm (${d.risk})`], ["Data source", d.source], ["CCTV confirmation", d.cctv_confirmed ? "Sample detection" : "None"]]
    : selected.kind === "node" ? [["Type / location", `${d.kind || "Drainage node"} · ${d.label || d.node_id}`], ["Capacity utilization", `${d.utilization}%`], ["Water depth", `${(d.predicted_depth_m * 100).toFixed(1)} cm`], ["Risk", d.risk], ["Data source", d.source]]
    : selected.kind === "hotspot" ? [["Flood depth", `${(d.depth_m * 100).toFixed(1)} cm`], ["Nearest drainage", d.node_id], ["Risk", d.risk], ["Data source", d.source]]
    : [["Data source", "Backend scenario output"], ["System state", "Operational"], ["Rainfall event total", `${rainfall.toFixed(1)} mm`]];
  return <div className="info-panel"><button className="close-button" onClick={onClose}>×</button>{selected.kind === "camera" ? <><div className="eyebrow">Sample CCTV observation</div><h2>{d.camera_id}</h2><div className="status-badge">{d.status}</div><div className="video-shell">{canPlayCamera ? <><video key={d.video_url} controls autoPlay muted playsInline preload="metadata" src={d.video_url} /><div>{d.camera_id}<br />SYNTHETIC CCTV SAMPLE<br />{d.status} DETECTED</div></> : <div className="camera-paused">Sample feed: no waterlogging detected.</div>}</div><dl><dt>Location</dt><dd>{d.location}</dd><dt>Road</dt><dd>{d.road_id || "Unmapped"}</dd><dt>Estimated depth</dt><dd>{(d.depth_m * 100).toFixed(1)} cm · heuristic proxy</dd><dt>Detection confidence</dt><dd>{d.confidence}%</dd><dt>Data source</dt><dd>{d.source}</dd></dl></> : <><div className="eyebrow">{selected.kind === "architecture" ? "System component" : selected.kind === "node" ? "Drainage node" : selected.kind === "hotspot" ? "Flood hotspot" : "Road information"}</div><h2>{selected.kind === "architecture" ? d.name : selected.kind === "hotspot" ? d.id : selected.kind === "node" ? d.node_id : d.road_id}</h2>{d.status || d.risk ? <div className="status-badge">{d.status || d.risk}</div> : null}<dl>{rows.map(([label, value]) => <><dt key={label + "dt"}>{label}</dt><dd key={label + "dd"}>{value}</dd></>)}</dl></>}</div>;
}
