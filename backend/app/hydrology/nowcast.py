"""
Turns the timestep-indexed simulation log into the T+0/15/30/60/120/180
minute nowcast product required by spec section 8.

The core simulation runs at 1-hour timesteps (matching the hourly rainfall
hyetograph — see ingestion.synthetic_catchment.generate_rainfall_event).
15/30-minute nowcast points are produced by linear interpolation between
hourly simulation states, which is an approximation, not a sub-hourly
physical solve; this is flagged explicitly rather than presented as
higher-resolution physics.
"""
from __future__ import annotations
import numpy as np
from app.core.schemas import RiskThresholds
from app.hydrology.simulation import depth_to_risk

NOWCAST_MINUTES = [0, 15, 30, 60, 120, 180]


def interpolate_depth_series(timestep_log: list[dict], timestep_minutes: int = 60) -> dict:
    """timestep_log: list of {"t_index": i, "depths_m": {node: depth}}
    Returns {node_id: {minute: depth_m}} for minute in NOWCAST_MINUTES,
    via linear interpolation/extrapolation (last value held beyond the end
    of the simulated log)."""
    node_ids = set()
    for entry in timestep_log:
        node_ids.update(entry["depths_m"].keys())

    series_by_node = {n: [] for n in node_ids}
    times_min = [0] + [
        (entry["t_index"] + 1) * timestep_minutes for entry in timestep_log
    ]
    for entry in timestep_log:
        for n in node_ids:
            series_by_node[n].append(entry["depths_m"].get(n, 0.0))

    result = {}
    for n in node_ids:
        ys = [0.0] + series_by_node[n]
        xs = times_min
        interp = {}
        for m in NOWCAST_MINUTES:
            if m <= xs[-1]:
                interp[m] = float(np.interp(m, xs, ys))
            else:
                interp[m] = float(ys[-1])  # hold last known state
        result[n] = interp
    return result


def build_nowcast_response(node_depth_series: dict, thresholds: RiskThresholds | None = None,
                           node_utilization_series: dict | None = None) -> list[dict]:
    thresholds = thresholds or RiskThresholds()
    out = []
    for node_id, minute_depths in node_depth_series.items():
        forecast = []
        onset_minute = None
        for m in NOWCAST_MINUTES:
            depth = minute_depths[m]
            risk = depth_to_risk(depth, thresholds)
            if onset_minute is None and risk != "NONE":
                onset_minute = m
            forecast.append({
                "minute": m, "predicted_depth_m": round(depth, 3), "risk": risk,
                "confidence": _confidence_for_horizon(m),
                "capacity_utilization_pct": (
                    round(node_utilization_series.get(node_id, {}).get(m, 0.0), 1)
                    if node_utilization_series else None
                ),
            })
        out.append({
            "node_id": node_id,
            "forecast": forecast,
            "expected_onset_minute": onset_minute,
            "risk_thresholds_validated": thresholds.validated,
        })
    return out


def _confidence_for_horizon(minute: int) -> float:
    """Confidence decays with forecast horizon — a simple, explicit,
    non-learned decay curve (NOT derived from a validated skill score,
    since no verified accuracy-vs-lead-time study exists for this
    prototype). See docs/limitations.md."""
    return round(float(max(0.35, 0.9 - 0.9 * (minute / 180))), 2)
