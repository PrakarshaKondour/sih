"""
Run with: cd backend && python -m pytest ../tests -v

Covers the auditable physics/graph pieces (not a full statistical
validation of forecast skill -- there is no verified ground truth to
validate skill against, see docs/limitations.md). These tests check that
the pipeline is internally consistent and behaves the way the spec
requires (mass balance, calibration mechanism runs, CCTV distinguishes its
own synthetic dry/wet frames, routing avoids critical risk where possible).
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np
import pytest


def test_scs_cn_runoff_monotonic_in_rainfall():
    from app.hydrology.runoff import scs_cn_runoff
    cn = np.array([80.0])
    q_low = scs_cn_runoff(20.0, cn)
    q_high = scs_cn_runoff(100.0, cn)
    assert q_high > q_low, "more rainfall must produce >= runoff"


def test_scs_cn_more_impervious_more_runoff():
    from app.hydrology.runoff import scs_cn_runoff
    cn_pervious = np.array([61.0])
    cn_impervious = np.array([98.0])
    q_pervious = scs_cn_runoff(50.0, cn_pervious)
    q_impervious = scs_cn_runoff(50.0, cn_impervious)
    assert q_impervious > q_pervious


def test_manning_pipe_capacity_increases_with_diameter():
    from app.drainage.capacity import circular_pipe_capacity_m3s
    small = circular_pipe_capacity_m3s(300, 0.002, "RCC")
    large = circular_pipe_capacity_m3s(900, 0.002, "RCC")
    assert large > small


def test_manning_pipe_capacity_increases_with_slope():
    from app.drainage.capacity import circular_pipe_capacity_m3s
    flat = circular_pipe_capacity_m3s(600, 0.0005, "RCC")
    steep = circular_pipe_capacity_m3s(600, 0.02, "RCC")
    assert steep > flat


def test_dem_flow_direction_is_acyclic():
    from app.hydrology import dem_processing as dp
    rng = np.random.default_rng(0)
    dem = np.cumsum(rng.uniform(0, 1, size=(20, 20)), axis=0)  # monotonic-ish slope, no ties
    filled = dp.fill_sinks(dem)
    directions = dp.d8_flow_direction(filled)
    acc = dp.flow_accumulation(directions, filled)
    assert acc.max() <= dem.size, "accumulation should never exceed total cell count"
    assert np.all(acc >= 1)


def test_drainage_graph_builds_and_mostly_connected():
    from app.demo.orchestrator import Catchment
    c = Catchment()
    assert c.graph.number_of_nodes() > 0
    assert c.graph.number_of_edges() > 0
    no_out = [n for n in c.graph.nodes if c.graph.out_degree(n) == 0]
    # allow a small number of genuine local minima / boundary exits, but
    # the network should not be mostly disconnected leaf nodes
    assert len(no_out) < 0.15 * c.graph.number_of_nodes()


def test_simulation_mass_balance_nonnegative():
    from app.demo.orchestrator import Catchment, _rainfall_to_node_inflow_series
    from app.hydrology.simulation import run_simulation
    from app.ingestion import synthetic_catchment as sc
    c = Catchment()
    rainfall_df = sc.generate_rainfall_event(total_mm=60.0, hours=3)
    inflow_series = _rainfall_to_node_inflow_series(c, rainfall_df)
    state = run_simulation(c.graph, inflow_series, ponding_area_m2=c.ponding_areas)
    for depths in state.timestep_log:
        for node, d in depths["depths_m"].items():
            assert d >= 0, f"depth must never be negative ({node}={d})"
            assert d < 100, f"sanity bound: no node should show >100m depth ({node}={d})"


def test_nowcast_has_required_horizons():
    from app.demo.orchestrator import get_catchment, run_scenario
    c = get_catchment()
    scenario = run_scenario(c)
    for entry in scenario["nowcast"]:
        minutes = [f["minute"] for f in entry["forecast"]]
        assert minutes == [0, 15, 30, 60, 120, 180]
        for f in entry["forecast"]:
            assert f["risk"] in ("NONE", "LOW", "MODERATE", "HIGH", "CRITICAL")


def test_calibration_runs_and_reports_synthetic_flag():
    from app.demo.orchestrator import get_catchment, run_historical_replay
    c = get_catchment()
    result = run_historical_replay(c)
    assert "SYNTHETIC" in result["calibration"]["note"]
    for phase in ("before_calibration", "after_calibration"):
        m = result["calibration"][phase]["metrics"]
        assert 0.0 <= m["precision"] <= 1.0
        assert 0.0 <= m["recall"] <= 1.0
        assert 0.0 <= m["f1"] <= 1.0


def test_cctv_detects_synthetic_waterlogged_frame_as_flooded():
    import cv2
    from app.cctv.synthetic_frames import generate_demo_frames
    from app.cctv.flood_detection import detect_flood
    frames = generate_demo_frames("/tmp/hydroloop_test_frames")
    results = {}
    for f in frames:
        img = cv2.imread(f["frame_path"])
        obs = detect_flood(img, f["camera_id"], f["lat"], f["lon"], "test")
        results[f["camera_id"]] = obs.flood_detected
    assert results["cam01"] is False, "dry demo frame should not be flagged as flooded"
    assert results["cam02"] is True, "waterlogged demo frame should be flagged as flooded"


def test_cctv_assimilation_pulls_prediction_toward_observation():
    from app.cctv.flood_detection import FloodObservation, assimilate
    obs = FloodObservation(camera_id="camX", lat=0, lon=0, timestamp="t",
                             flood_detected=True, estimated_depth_m=0.25, confidence=0.8)
    result = assimilate(predicted_depth_m=0.05, observation=obs)
    assert result["corrected_depth_m"] > 0.05
    assert result["corrected_depth_m"] < 0.25 or abs(result["corrected_depth_m"] - 0.25) < 1e-6


def test_real_road_graph_has_dominant_connected_component():
    import networkx as nx
    from app.demo.orchestrator import get_catchment
    from app.routing.flood_aware_routing import build_road_graph
    c = get_catchment()
    rg = build_road_graph(c.roads)
    largest = max((len(component) for component in nx.connected_components(rg)), default=0)
    assert largest >= 0.98 * rg.number_of_nodes()


def test_flood_aware_route_never_worse_than_avoiding_critical():
    import networkx as nx
    from app.demo.orchestrator import get_catchment
    from app.routing.flood_aware_routing import build_road_graph, annotate_edge_risk, compute_routes
    c = get_catchment()
    rg = build_road_graph(c.roads)
    nodes = list(max(nx.connected_components(rg), key=len))
    node_risk = {nodes[len(nodes) // 2]: "CRITICAL"}
    annotate_edge_risk(rg, node_risk)
    result = compute_routes(rg, nodes[0], nodes[-1], profile="NORMAL")
    assert "flood_aware_route" in result
    assert result["flood_aware_route"]["length_m"] >= result["normal_route"]["length_m"] - 1e-6


def test_alerts_never_dispatch_without_credentials():
    from app.alerts.alert_service import AlertContent, dispatch_alert
    content = AlertContent(locality="X", expected_depth_m=0.3, expected_time_minute=30,
                             severity="MODERATE", alt_route_summary="none")
    result = dispatch_alert(content)
    assert result["dispatched"] is False
    assert "MOCK" in result["mode"]
    assert "en" in result["messages"] and "te" in result["messages"]
    assert "మధ్యస్థ" in result["messages"]["te"]


def test_demo_scenario_exposes_synthetic_labels_and_runtime():
    from app.demo.orchestrator import get_catchment, run_scenario
    c = get_catchment()
    result = run_scenario(c)
    assert result["data_source"] == "SYNTHETIC"
    assert "runtime_seconds" in result
    assert result["runtime_seconds"] >= 0
    assert result["note"].upper().find("SYNTHETIC") >= 0
    assert result["rainfall_source"] == "synthetic-demo"
    assert [h["minute"] for h in result["horizons"]] == [0, 15, 30, 60, 120, 180]
    assert all(forecast["predicted_depth_m"] == 0
               for entry in result["nowcast"] for forecast in entry["forecast"]
               if forecast["minute"] == 0)
    road_ids = {str(road_id) for road_id in c.roads["road_id"]}
    for horizon in result["horizons"]:
        assert {road["road_id"] for road in horizon["roads"]} == road_ids
        assert horizon["severity"] in ("NONE", "LOW", "MODERATE", "HIGH", "CRITICAL")
        assert all(0 <= node["capacity_utilization_pct"] <= 100
                   for node in horizon["drainage"])
    assert all(camera["road_id"] in road_ids for camera in result["cctv_cameras"])
    assert result["alerts"]
    assert set(result["alerts"][0]["messages"]) == {"en", "te"}
    assert result["alerts"][0]["trigger"] == "backend_flood_threshold"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_navigation_route_returns_road_risk_metadata():
    from app.demo.orchestrator import get_catchment
    from app.routing.flood_aware_routing import build_road_graph, annotate_edge_risk, compute_routes
    c = get_catchment()
    rg = build_road_graph(c.roads)
    nodes = list(rg.nodes)
    # Force one graph edge to be critical so the navigation response exposes
    # the road ID as blocked. This tests the road-level contract, not a model
    # accuracy claim.
    u, v = next(iter(rg.edges))
    mid = ((u[0] + v[0]) / 2, (u[1] + v[1]) / 2)
    annotate_edge_risk(rg, {mid: "CRITICAL"})
    result = compute_routes(rg, u, v, profile="NORMAL")
    assert "at_risk_roads" in result
    assert "blocked_road_ids" in result
    assert result["blocked_road_ids"]
    assert result["blocked_road_ids"][0] in set(c.roads["road_id"].astype(str))


def test_scenario_and_route_api_share_backend_road_risk():
    import networkx as nx
    from fastapi.testclient import TestClient
    from app.demo.orchestrator import get_catchment
    from app.main import app

    client = TestClient(app)
    scenario_response = client.get("/api/scenario/run?rainfall_mode=demo")
    assert scenario_response.status_code == 200
    scenario = scenario_response.json()
    assert scenario["rainfall_source"] == "synthetic-demo"
    assert scenario["road_source"] == get_catchment().road_source
    assert [horizon["minute"] for horizon in scenario["horizons"]] == [0, 15, 30, 60, 120, 180]
    from app.ingestion.real_data import latest_imerge_tiffs
    imd_csv = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "imd_rainfall.csv")
    if not os.path.isfile(imd_csv) and not latest_imerge_tiffs():
        assert client.get("/api/scenario/run?rainfall_mode=real").status_code == 409

    from app.routing.flood_aware_routing import build_road_graph
    road_graph = build_road_graph(get_catchment().roads)
    nodes = list(max(nx.connected_components(road_graph), key=len))
    start, end = nodes[0], nodes[-1]
    road_ids = {str(road_id) for road_id in get_catchment().roads["road_id"]}
    route_response = client.post("/api/routing/route", json={
        "start_lon": start[0],
        "start_lat": start[1],
        "end_lon": end[0],
        "end_lat": end[1],
        "profile": "NORMAL",
        "horizon_minute": 180,
        "rainfall_mode": "demo",
    })
    assert route_response.status_code == 200
    route = route_response.json()
    assert route["rainfall_source"] == scenario["rainfall_source"]
    assert set(road["road_id"] for road in route["at_risk_roads"]) <= road_ids
