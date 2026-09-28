const BASE = "/api";

export async function getLayers() {
  const r = await fetch(`${BASE}/catchment/layers`);
  return r.json();
}

export async function runScenario(rainfallMode: "real" | "demo" = "demo") {
  const r = await fetch(`${BASE}/scenario/run?rainfall_mode=${rainfallMode}`);
  if (!r.ok) throw new Error((await r.json()).detail || "Could not run flood scenario");
  return r.json();
}

export async function getDataStatus() {
  const r = await fetch(`${BASE}/data/status`);
  return r.json();
}

export async function runHistoricalReplay() {
  const r = await fetch(`${BASE}/scenario/historical-replay`);
  return r.json();
}

export interface RouteRequest {
  start_lon: number;
  start_lat: number;
  end_lon: number;
  end_lat: number;
  profile: "NORMAL" | "AMBULANCE" | "FIRE";
  horizon_minute: number;
  rainfall_mode: "real" | "demo";
}

export async function computeRoute(req: RouteRequest) {
  const r = await fetch(`${BASE}/routing/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return r.json();
}

export interface AlertRequest {
  locality: string;
  expected_depth_m: number;
  expected_time_minute: number;
  severity: string;
  alt_route_summary?: string;
}

export async function dispatchAlert(req: AlertRequest) {
  const r = await fetch(`${BASE}/alerts/dispatch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return r.json();
}
