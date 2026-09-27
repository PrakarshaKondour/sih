import { useEffect, useState } from "react";
import MapView, { LayerData, NowcastEntry } from "./components/MapView";
import { getLayers, runScenario, runHistoricalReplay, computeRoute, dispatchAlert } from "./api";

const MINUTES = [0, 15, 30, 60, 120, 180];

type Mode = "idle" | "scenario" | "replay";
type Point = { lat: number; lon: number };

type RouteResult = {
  normal_route: { path: number[][]; length_m: number; road_ids: string[]; segments: any[] };
  flood_aware_route: { path: number[][]; length_m: number; road_ids: string[]; segments: any[] };
  detour_added_m: number;
  reroute_required: boolean;
  at_risk_roads: { road_id: string; risk: string }[];
  blocked_road_ids: string[];
  blocked_on_normal_route: boolean;
  alternate_route_found: boolean;
  horizon_minute: number;
  data_status: string;
  data_note: string;
  profile: string;
};

export default function App() {
  const [layers, setLayers] = useState<LayerData | null>(null);
  const [nowcast, setNowcast] = useState<NowcastEntry[] | null>(null);
  const [minute, setMinute] = useState(60);
  const [mode, setMode] = useState<Mode>("idle");
  const [loading, setLoading] = useState(false);
  const [routeLoading, setRouteLoading] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [replayResult, setReplayResult] = useState<any>(null);
  const [routeResult, setRouteResult] = useState<RouteResult | null>(null);
  const [routeClicks, setRouteClicks] = useState<Point[]>([]);
  const [profile, setProfile] = useState<"NORMAL" | "AMBULANCE" | "FIRE">("NORMAL");
  const [dataStatus, setDataStatus] = useState<any>(null);
  const [visibleLayers, setVisibleLayers] = useState<Record<string, boolean>>({
    manholes: true,
    sewerlines: false,
    nala: true,
    roads: true,
    historical_flood: false,
    cctv: true,
    nowcast: true,
  });

  const appendLog = (s: string) =>
    setLog((l) => [`[${new Date().toLocaleTimeString()}] ${s}`, ...l].slice(0, 50));

  useEffect(() => {
    fetch("http://localhost:8000/data/status")
      .then((r) => r.json())
      .then(setDataStatus)
      .catch(() => undefined);
    getLayers()
      .then(setLayers)
      .catch(() => appendLog("Failed to load catchment layers — is the backend running on :8000?"));
  }, []);

  async function handleRunScenario() {
    setLoading(true);
    setMode("scenario");
    appendLog("Running demo flood scenario...");
    try {
      const result = await runScenario();
      setNowcast(result.nowcast);
      setVisibleLayers((v) => ({ ...v, nowcast: true }));
      appendLog(
        `Scenario complete. ${result.cctv_observations.filter((o: any) => o.flood_detected).length} camera(s) detected flooding.`
      );
      result.assimilation_results.forEach((a: any) => {
        appendLog(
          `Assimilation @ ${a.node_id}: predicted ${a.predicted_depth_m}m, CCTV observed ${a.observed_depth_m}m -> corrected ${a.corrected_depth_m}m`
        );
      });
      const worst = [...result.nowcast].sort(
        (a: any, b: any) => b.forecast[3].predicted_depth_m - a.forecast[3].predicted_depth_m
      )[0];
      if (worst) {
        await dispatchAlert({
          locality: `near ${worst.node_id}`,
          expected_depth_m: worst.forecast[3].predicted_depth_m,
          expected_time_minute: worst.expected_onset_minute ?? 60,
          severity: worst.forecast[3].risk,
          alt_route_summary: "see flood-safe navigation panel",
        }).then((a) => appendLog(`Alert generated (${a.mode}): ${a.messages.en}`));
      }
    } catch {
      appendLog("Scenario run failed — check backend logs.");
    }
    setLoading(false);
  }

  async function handleHistoricalReplay() {
    setLoading(true);
    setMode("replay");
    appendLog("Replaying 13 Oct 2020 rainfall profile...");
    try {
      const result = await runHistoricalReplay();
      setReplayResult(result);
      setVisibleLayers((v) => ({ ...v, historical_flood: true }));
      const before = result.calibration.before_calibration.metrics;
      const after = result.calibration.after_calibration.metrics;
      appendLog(`BEFORE calibration: precision ${before.precision}, recall ${before.recall}, F1 ${before.f1}`);
      appendLog(`AFTER calibration: precision ${after.precision}, recall ${after.recall}, F1 ${after.f1}`);
      appendLog(result.calibration.note);
    } catch {
      appendLog("Historical replay failed — check backend logs.");
    }
    setLoading(false);
  }

  function useMyLocation() {
    if (!navigator.geolocation) {
      appendLog("Browser geolocation is not available.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const point = { lat: position.coords.latitude, lon: position.coords.longitude };
        setRouteClicks([point]);
        setRouteResult(null);
        appendLog(`GPS start selected: ${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`);
      },
      () => appendLog("Location permission denied. Click the map to select a start point."),
      { enableHighAccuracy: true, timeout: 10000 }
    );
  }

  function clearNavigation() {
    setRouteClicks([]);
    setRouteResult(null);
    appendLog("Navigation points cleared.");
  }

  async function calculateRoute(points = routeClicks) {
    if (points.length !== 2) {
      appendLog("Select a start point and destination first.");
      return;
    }
    setRouteLoading(true);
    appendLog(`Calculating flood-aware route for T+${minute} min...`);
    try {
      const r = await computeRoute({
        start_lon: points[0].lon,
        start_lat: points[0].lat,
        end_lon: points[1].lon,
        end_lat: points[1].lat,
        profile,
        horizon_minute: minute,
      });
      if (r.error) {
        appendLog(`Routing error: ${r.error}`);
        return;
      }
      setRouteResult(r);
      if (r.reroute_required) {
        appendLog(`⚠ Flood risk found on the normal route. Alternate route adds ${r.detour_added_m}m.`);
      } else {
        appendLog("No flood-driven route change was required for this horizon.");
      }
      if (r.blocked_road_ids.length) {
        appendLog(`Blocked road IDs: ${r.blocked_road_ids.join(", ")}`);
      }
    } catch {
      appendLog("Routing request failed.");
    } finally {
      setRouteLoading(false);
    }
  }

  function handleMapClick(lat: number, lon: number) {
    if (routeClicks.length >= 2) {
      setRouteClicks([{ lat, lon }]);
      setRouteResult(null);
      appendLog("New start point selected. Click the destination next.");
      return;
    }
    const next = [...routeClicks, { lat, lon }];
    setRouteClicks(next);
    if (next.length === 1) appendLog("Start selected. Click the destination.");
    if (next.length === 2) appendLog("Destination selected. Click Calculate Route.");
  }

  const routeForMap = routeResult
    ? {
        normal: routeResult.normal_route.path.map((p) => [p[1], p[0]] as [number, number]),
        aware: routeResult.flood_aware_route.path.map((p) => [p[1], p[0]] as [number, number]),
        atRiskRoadIds: routeResult.at_risk_roads.map((r) => r.road_id),
        blockedRoadIds: routeResult.blocked_road_ids,
      }
    : null;

  return (
    <div className="app">
      <div className="header">
        <span className="logo">HYDROLOOP</span>
        <span className="subtitle">Urban Flood Nowcasting + Flood-Safe Navigation — SIH PS 26085</span>
        <span className="badge">
          {dataStatus?.mode === "real"
            ? "REAL DATA MODE"
            : "DEMO MODE · synthetic inputs"}
        </span>
      </div>

      <div className="sidebar">
        <div className="section">
          <h3>Demo modes</h3>
          <button className="btn" disabled={loading} onClick={handleRunScenario}>▶ Run Flood Scenario</button>
          <button className="btn" disabled={loading} onClick={handleHistoricalReplay}>⟲ Historical Event Replay</button>
          {loading && <div className="status-line">Running...</div>}
        </div>

        <div className="section navigation-section">
          <h3>🚗 Flood-safe navigation</h3>
          <div className="panel-note">
            Set your current location and destination. The system compares the shortest route with a flood-risk-aware alternative.
          </div>
          <div className="data-status-card">
            <b>Data source</b>
            <span>
              {dataStatus?.mode === "real"
                ? "REAL roads + REAL DEM"
                : "Synthetic demo roads + DEM"}
            </span>
          </div>
          <div className="nav-actions">
            <button className="btn primary" onClick={useMyLocation}>📍 Use my location</button>
            <button className="btn" onClick={clearNavigation}>Clear</button>
          </div>
          <div className="route-point">
            <b>Start</b>
            <span>{routeClicks[0] ? `${routeClicks[0].lat.toFixed(5)}, ${routeClicks[0].lon.toFixed(5)}` : "Click map or use GPS"}</span>
          </div>
          <div className="route-point">
            <b>Destination</b>
            <span>{routeClicks[1] ? `${routeClicks[1].lat.toFixed(5)}, ${routeClicks[1].lon.toFixed(5)}` : "Click map"}</span>
          </div>
          <select
            value={profile}
            onChange={(e) => setProfile(e.target.value as any)}
            className="select"
          >
            <option value="NORMAL">🚗 Normal user</option>
            <option value="AMBULANCE">🚑 Ambulance</option>
            <option value="FIRE">🚒 Fire / emergency</option>
          </select>
          <select value={minute} onChange={(e) => setMinute(Number(e.target.value))} className="select">
            {MINUTES.map((m) => <option key={m} value={m}>Flood forecast: T+{m} min</option>)}
          </select>
          <button
            className="btn primary full"
            disabled={routeLoading || routeClicks.length !== 2}
            onClick={() => calculateRoute()}
          >
            {routeLoading ? "Checking flood risk..." : "Calculate Safe Route"}
          </button>

          {routeResult && (
            <div className={`route-result ${routeResult.reroute_required ? "reroute" : "safe"}`}>
              <div className="route-result-title">
                {routeResult.reroute_required
                  ? "⚠ Alternate route recommended"
                  : routeResult.blocked_on_normal_route
                    ? "⚠ Flooded road detected — no alternate path found in this graph"
                    : "✓ No reroute required"}
              </div>
              <div className="route-stats">
                <span>Normal <b>{(routeResult.normal_route.length_m / 1000).toFixed(2)} km</b></span>
                <span>Safe <b>{(routeResult.flood_aware_route.length_m / 1000).toFixed(2)} km</b></span>
              </div>
              {routeResult.reroute_required && <div>Extra distance: <b>+{routeResult.detour_added_m} m</b></div>}
              {routeResult.at_risk_roads.length > 0 && (
                <div className="risk-roads">
                  <b>Risk on normal route</b>
                  {routeResult.at_risk_roads.map((r) => <div key={r.road_id}>• {r.road_id}: {r.risk}</div>)}
                </div>
              )}
              <small>{routeResult.data_status}: routing prototype</small>
            </div>
          )}
        </div>

        <div className="section">
          <h3>Layers</h3>
          {Object.keys(visibleLayers).map((k) => (
            <label className="layer-toggle" key={k}>
              <span>{k}</span>
              <input
                type="checkbox"
                checked={visibleLayers[k]}
                onChange={(e) => setVisibleLayers((v) => ({ ...v, [k]: e.target.checked }))}
              />
            </label>
          ))}
        </div>

        <div className="section">
          <h3>Legend</h3>
          <div className="legend-item"><span className="dot" style={{ background: "#38bdf8" }} /> real infrastructure</div>
          <div className="legend-item"><span className="dot" style={{ background: "#fb923c" }} /> synthetic-demo</div>
          <div className="legend-item"><span className="dot" style={{ background: "#a78bfa" }} /> DEM-inferred</div>
          <div className="legend-item"><span className="dot" style={{ background: "#22d3ee" }} /> CCTV camera</div>
          {visibleLayers.nowcast && <>
            <div className="legend-item"><span className="dot" style={{ background: "#4ade80" }} /> LOW</div>
            <div className="legend-item"><span className="dot" style={{ background: "#facc15" }} /> MODERATE</div>
            <div className="legend-item"><span className="dot" style={{ background: "#fb923c" }} /> HIGH</div>
            <div className="legend-item"><span className="dot" style={{ background: "#ef4444" }} /> CRITICAL</div>
          </>}
        </div>

        {replayResult && <div className="section">
          <h3>Calibration result</h3>
          <div className="panel-note">
            Before: F1 {replayResult.calibration.before_calibration.metrics.f1} → After: F1 {replayResult.calibration.after_calibration.metrics.f1}<br />
            Holdout transfer F1: {replayResult.holdout_validation.metrics.f1}<br />
            {replayResult.calibration.note}
          </div>
        </div>}

        <div className="section">
          <h3>Event log</h3>
          <div className="log-box">{log.length ? log.join("\n") : "No events yet."}</div>
        </div>
      </div>

      <div className="map-wrap">
        <MapView
          layers={layers}
          nowcast={nowcast}
          minute={minute}
          visibleLayers={visibleLayers}
          route={routeForMap}
          routeClicks={routeClicks}
          onMapClick={handleMapClick}
        />
      </div>

      <div className="timeline">
        <div className="slider-row">
          <span className="slider-label">NOW</span>
          <input
            type="range"
            min={0}
            max={MINUTES.length - 1}
            step={1}
            value={MINUTES.indexOf(minute)}
            onChange={(e) => setMinute(MINUTES[Number(e.target.value)])}
          />
          <span className="slider-label">T+180 min</span>
        </div>
        <div className="status-line">
          Flood horizon: T+{minute} min {nowcast ? "· nowcast loaded" : "· run the scenario to visualize node forecasts"}
        </div>
      </div>
    </div>
  );
}
