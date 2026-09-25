import { useEffect, useState } from "react";
import MapView, { LayerData, NowcastEntry } from "./components/MapView";
import { getLayers, runScenario, runHistoricalReplay, computeRoute, dispatchAlert } from "./api";

const MINUTES = [0, 15, 30, 60, 120, 180];

type Mode = "idle" | "scenario" | "replay";

export default function App() {
  const [layers, setLayers] = useState<LayerData | null>(null);
  const [nowcast, setNowcast] = useState<NowcastEntry[] | null>(null);
  const [minute, setMinute] = useState(0);
  const [mode, setMode] = useState<Mode>("idle");
  const [loading, setLoading] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [replayResult, setReplayResult] = useState<any>(null);
  const [route, setRoute] = useState<any>(null);
  const [routeClicks, setRouteClicks] = useState<{ lat: number; lon: number }[]>([]);
  const [profile, setProfile] = useState<"NORMAL" | "AMBULANCE" | "FIRE">("NORMAL");
  const [visibleLayers, setVisibleLayers] = useState<Record<string, boolean>>({
    manholes: true,
    sewerlines: true,
    nala: true,
    roads: true,
    historical_flood: false,
    cctv: true,
    nowcast: false,
  });

  const appendLog = (s: string) => setLog((l) => [`[${new Date().toLocaleTimeString()}] ${s}`, ...l].slice(0, 50));

  useEffect(() => {
    getLayers().then(setLayers).catch(() => appendLog("Failed to load catchment layers — is the backend running on :8000?"));
  }, []);

  async function handleRunScenario() {
    setLoading(true);
    setMode("scenario");
    appendLog("Running synthetic-demo storm scenario...");
    try {
      const result = await runScenario();
      setNowcast(result.nowcast);
      setVisibleLayers((v) => ({ ...v, nowcast: true }));
      appendLog(`Scenario complete. ${result.cctv_observations.filter((o: any) => o.flood_detected).length} camera(s) detected flooding.`);
      result.assimilation_results.forEach((a: any) => {
        appendLog(`Assimilation @ ${a.node_id}: predicted ${a.predicted_depth_m}m, CCTV observed ${a.observed_depth_m}m -> corrected ${a.corrected_depth_m}m`);
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
          alt_route_summary: "see flood-aware route panel",
        }).then((a) => appendLog(`Alert generated (${a.mode}): ${a.messages.en}`));
      }
    } catch (e) {
      appendLog("Scenario run failed — check backend logs.");
    }
    setLoading(false);
  }

  async function handleHistoricalReplay() {
    setLoading(true);
    setMode("replay");
    appendLog("Replaying 13 Oct 2020 rainfall profile (192mm/6h)...");
    try {
      const result = await runHistoricalReplay();
      setReplayResult(result);
      setVisibleLayers((v) => ({ ...v, historical_flood: true }));
      const before = result.calibration.before_calibration.metrics;
      const after = result.calibration.after_calibration.metrics;
      appendLog(`BEFORE calibration: precision ${before.precision}, recall ${before.recall}, F1 ${before.f1}`);
      appendLog(`AFTER calibration: precision ${after.precision}, recall ${after.recall}, F1 ${after.f1}`);
      appendLog(result.calibration.note);
    } catch (e) {
      appendLog("Historical replay failed — check backend logs.");
    }
    setLoading(false);
  }

  function handleMapClick(lat: number, lon: number) {
    const next = [...routeClicks, { lat, lon }].slice(-2);
    setRouteClicks(next);
    if (next.length === 2) {
      appendLog(`Computing ${profile} route...`);
      computeRoute({
        start_lon: next[0].lon,
        start_lat: next[0].lat,
        end_lon: next[1].lon,
        end_lat: next[1].lat,
        profile,
      })
        .then((r) => {
          if (r.error) {
            appendLog(`Routing error: ${r.error}`);
            return;
          }
          setRoute({
            normal: r.normal_route.path.map((p: number[]) => [p[1], p[0]]),
            aware: r.flood_aware_route.path.map((p: number[]) => [p[1], p[0]]),
          });
          appendLog(`Route found. Flood-aware detour: +${r.detour_added_m}m vs. shortest path.`);
        })
        .catch(() => appendLog("Routing request failed."));
    }
  }

  return (
    <div className="app">
      <div className="header">
        <span className="logo">HYDROLOOP</span>
        <span className="subtitle">Urban Flood Nowcasting — SIH PS 26085 — demo catchment, Hyderabad</span>
        <span className="badge">real infra: solid lines &nbsp;·&nbsp; synthetic-demo: dashed/orange</span>
      </div>

      <div className="sidebar">
        <div className="section">
          <h3>Demo modes</h3>
          <button className="btn primary" disabled={loading} onClick={handleRunScenario}>
            ▶ Run Flood Scenario
          </button>
          <button className="btn" disabled={loading} onClick={handleHistoricalReplay}>
            ⟲ Historical Event Replay (13 Oct 2020)
          </button>
          {loading && <div className="status-line">Running...</div>}
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
          {visibleLayers.nowcast && (
            <>
              <div className="legend-item"><span className="dot" style={{ background: "#4ade80" }} /> LOW risk</div>
              <div className="legend-item"><span className="dot" style={{ background: "#facc15" }} /> MODERATE</div>
              <div className="legend-item"><span className="dot" style={{ background: "#fb923c" }} /> HIGH</div>
              <div className="legend-item"><span className="dot" style={{ background: "#ef4444" }} /> CRITICAL</div>
            </>
          )}
        </div>

        <div className="section">
          <h3>Flood-aware routing</h3>
          <div className="panel-note">Click two points on the map to route between them (after running a scenario).</div>
          <select
            value={profile}
            onChange={(e) => setProfile(e.target.value as any)}
            style={{ width: "100%", marginTop: 8, padding: 6, background: "#17233a", color: "#e6edf5", border: "1px solid #24324c", borderRadius: 6 }}
          >
            <option value="NORMAL">NORMAL</option>
            <option value="AMBULANCE">AMBULANCE</option>
            <option value="FIRE">FIRE/EMERGENCY</option>
          </select>
        </div>

        {replayResult && (
          <div className="section">
            <h3>Calibration result</h3>
            <div className="panel-note">
              Before: F1 {replayResult.calibration.before_calibration.metrics.f1} &nbsp;→&nbsp;
              After: F1 {replayResult.calibration.after_calibration.metrics.f1}
              <br />
              Holdout transfer F1: {replayResult.holdout_validation.metrics.f1}
              <br />
              {replayResult.calibration.note}
            </div>
          </div>
        )}

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
          route={route}
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
          Showing nowcast at T+{minute} min {nowcast ? "" : "(run a scenario to populate the nowcast)"}
        </div>
      </div>
    </div>
  );
}
