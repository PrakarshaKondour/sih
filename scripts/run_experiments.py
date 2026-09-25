#!/usr/bin/env python3
"""
Runs the progressive experiment ladder required by spec section 13:
  A. Rainfall only (uniform depth, no spatial routing)
  B. Rainfall + DEM (spatial distribution via nearest-node assignment,
     but treat drainage as having effectively infinite capacity)
  C. Rainfall + DEM + drainage network (full capacity-constrained
     simulation, default effective-capacity priors)
  D. C + inverse capacity calibration
  E. D + CCTV assimilation (reports the assimilation correction applied at
     camera locations; does not change the graph-wide flood prediction --
     see cctv/flood_detection.py docstring on why this is a local state
     correction, not a capacity change)

All ground truth is the SYNTHETIC historical-flood polygon set (see
docs/historical_event_13oct2020.md) -- every metric printed here is
labeled SYNTHETIC VALIDATION and must not be read as real-world skill.

Usage: cd backend && python ../scripts/run_experiments.py
"""
import sys
import os
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np


def main():
    from app.demo.orchestrator import Catchment, _rainfall_to_node_inflow_series
    from app.ingestion import synthetic_catchment as sc
    from app.hydrology.simulation import run_simulation, DEFAULT_PONDING_AREA_M2
    from app.calibration.inverse_calibration import (
        nodes_within_observed_flood, predicted_flooded_nodes, classification_metrics, calibrate,
    )
    from app.cctv.synthetic_frames import generate_demo_frames
    from app.cctv.flood_detection import detect_flood, assimilate
    import cv2

    print("Building catchment (synthetic-demo data; see data_inventory.md)...")
    c = Catchment()
    rainfall_df = sc.generate_rainfall_event(event_name="2020-10-13", total_mm=192.0, hours=6)
    total_mm = float(rainfall_df["rainfall_mm"].sum())
    observed = nodes_within_observed_flood(c.graph, c.historical_flood)
    all_nodes = set(c.graph.nodes)
    depth_threshold_m = 0.15

    results = {}

    # --- A. Rainfall only: uniform depth across the whole catchment,
    # ignoring DEM/graph entirely
    total_catchment_area_m2 = c.cell_area_m2 * c.dem_transform["resolution"] ** 2
    cn_uniform = np.full_like(c.cn_raster(), c.cn_raster().mean())
    from app.hydrology.runoff import runoff_timeseries
    runoff_series_uniform = runoff_timeseries(rainfall_df["rainfall_mm"].to_numpy(), cn_uniform)
    total_runoff_mm = float(runoff_series_uniform.sum(axis=0).mean())
    uniform_depth_m = (total_runoff_mm / 1000.0 * total_catchment_area_m2) / total_catchment_area_m2
    predicted_a = all_nodes if uniform_depth_m >= depth_threshold_m else set()
    results["A_rainfall_only"] = {
        "description": "uniform depth from total runoff / total catchment area, no spatial routing",
        "uniform_depth_m": round(uniform_depth_m, 3),
        "metrics": classification_metrics(predicted_a, observed, all_nodes),
    }

    # --- B. Rainfall + DEM: spatial distribution via nearest-node
    # assignment, but capacity treated as effectively infinite (so nothing
    # ever backs up -- isolates what spatial distribution alone buys you)
    inflow_series = _rainfall_to_node_inflow_series(c, rainfall_df)
    infinite_cap_overrides = {}
    for u, v, attrs in c.graph.edges(data=True):
        eid = attrs.get("edge_id", f"{u}->{v}")
        infinite_cap_overrides[eid] = 1e6  # effectively unlimited multiplier
        attrs["edge_id"] = eid
    state_b = run_simulation(c.graph, inflow_series, effective_factor_override=infinite_cap_overrides,
                                ponding_area_m2=c.ponding_areas)
    predicted_b = predicted_flooded_nodes(state_b, depth_threshold_m)
    results["B_rainfall_plus_dem"] = {
        "description": "spatially distributed runoff (DEM-nearest-node), infinite drainage capacity",
        "metrics": classification_metrics(predicted_b, observed, all_nodes),
    }

    # --- C. + drainage network (real capacity priors)
    state_c = run_simulation(c.graph, inflow_series, ponding_area_m2=c.ponding_areas)
    predicted_c = predicted_flooded_nodes(state_c, depth_threshold_m)
    results["C_plus_drainage_network"] = {
        "description": "capacity-constrained simulation, default effective-capacity priors",
        "metrics": classification_metrics(predicted_c, observed, all_nodes),
    }

    # --- D. + inverse calibration
    calib = calibrate(c.graph, inflow_series, c.historical_flood, depth_threshold_m=depth_threshold_m,
                        ponding_area_m2=c.ponding_areas, max_iter=30)
    results["D_plus_inverse_calibration"] = {
        "description": "capacity factors optimized via scipy.optimize (Nelder-Mead)",
        "metrics": calib["after_calibration"]["metrics"],
        "calibrated_factors": calib["after_calibration"]["factors"],
    }

    # --- E. + CCTV assimilation (local correction at camera nodes; report
    # the correction itself, since assimilation is a state update not a
    # graph-wide capacity change -- see module docstring)
    frames = generate_demo_frames(c.cctv_frames_dir)
    node_lookup = c.node_lonlat_lookup()
    node_coords = np.array(list(node_lookup.keys()))
    node_ids = list(node_lookup.values())
    assim_results = []
    for f in frames:
        img = cv2.imread(f["frame_path"])
        obs = detect_flood(img, f["camera_id"], f["lat"], f["lon"], "T+60min")
        if obs.flood_detected:
            d2 = (node_coords[:, 0] - obs.lon) ** 2 + (node_coords[:, 1] - obs.lat) ** 2
            nearest = node_ids[int(np.argmin(d2))]
            predicted_depth = state_c.node_depth_m.get(nearest, 0.0)
            r = assimilate(predicted_depth, obs)
            r["node_id"] = nearest
            assim_results.append(r)
    results["E_plus_cctv_assimilation"] = {
        "description": "local state correction at camera-adjacent nodes (not a graph-wide re-run)",
        "assimilation_corrections": assim_results,
    }

    results["_meta"] = {
        "event": "2020-10-13 profile (192mm/6h)",
        "depth_threshold_m": depth_threshold_m,
        "n_observed_flooded_nodes": len(observed),
        "n_total_nodes": len(all_nodes),
        "note": "SYNTHETIC VALIDATION throughout -- ground truth is synthetic. "
                "depth MAE/RMSE and spatial IoU are NOT reported: no per-node "
                "observed depth exists in this repo to compute them against "
                "(see docs/historical_event_13oct2020.md).",
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "docs", "experiment_results.json")
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    print(json.dumps(results, indent=2))
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
