const BASE = "/api";

export async function getLayers() {
  const r = await fetch(`${BASE}/catchment/layers`);
  return r.json();
}

export async function runScenario() {
  const r = await fetch(`${BASE}/scenario/run`);
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
