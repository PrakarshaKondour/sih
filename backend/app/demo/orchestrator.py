"""
Ties every module together into the two demo modes required by spec
section 12: "Run Flood Scenario" (a live-ish synthetic storm) and
"Historical Event Replay" (13 Oct 2020 profile -> calibration ->
before/after). Also builds the one shared catchment (spec section 15: "one
manageable Hyderabad catchment") used by every endpoint, cached in memory
so repeated API calls don't rebuild the graph each time.
"""
from __future__ import annotations
import numpy as np
import networkx as nx
import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import Point

from app.core.schemas import CatchmentBounds, RiskThresholds
from app.ingestion import synthetic_catchment as sc
from app.ingestion import real_data as rd
import os
from pathlib import Path
from app.drainage.graph_builder import build_drainage_graph, add_inferred_edges_from_low_points
from app.hydrology import dem_processing as dp
from app.hydrology.runoff import imperviousness_to_cn, runoff_timeseries
from app.hydrology.simulation import (
    run_simulation, assign_runoff_to_nearest_node, compute_node_ponding_areas,
)
from app.hydrology.nowcast import interpolate_depth_series, build_nowcast_response
from app.hydrology.nowcast import NOWCAST_MINUTES
from app.calibration.inverse_calibration import calibrate
from app.calibration.event_validation import build_holdout_event, validate_transfer
from app.cctv.synthetic_frames import generate_demo_frames
from app.cctv.flood_detection import detect_flood
from app.cctv import flood_detection as cctv_mod
from app.routing.flood_aware_routing import build_road_graph, annotate_edge_risk
from app.alerts.alert_service import AlertContent, dispatch_alert
import cv2


class Catchment:
    """Built once, reused across requests. This IS the "digital twin"."""

    def __init__(self):
        self.bounds = CatchmentBounds()
        self.data_mode = os.getenv("HYDROLOOP_DATA_MODE", "synthetic").lower()

        if self.data_mode == "real":
            # Strict mode: do not silently fall back to synthetic roads/DEM.
            self.roads = rd.load_roads(self.bounds)
            self.dem, self.dem_transform = rd.load_dem(self.bounds)

            # TGRAC drainage adapters still require a live schema probe and
            # explicit mapping. Keep the existing synthetic drainage graph
            # only until those verified real layers are downloaded.
            self.manholes = sc.generate_manholes(self.bounds)
            self.sewerlines = sc.generate_sewerlines(self.manholes, self.bounds)
            self.nala = sc.generate_trunk_nala(self.bounds)
            self.connectors = sc.snap_sewer_outfalls_to_nala(
                self.sewerlines, self.nala, self.manholes
            )
            self.historical_flood = sc.generate_historical_inundation(self.bounds)
            self.landcover_classes, self.imperviousness = sc.generate_landcover(
                self.bounds, resolution=max(30, int(self.dem.shape[0]))
            )
        else:
            self.manholes = sc.generate_manholes(self.bounds)
            self.sewerlines = sc.generate_sewerlines(self.manholes, self.bounds)
            self.nala = sc.generate_trunk_nala(self.bounds)
            self.connectors = sc.snap_sewer_outfalls_to_nala(self.sewerlines, self.nala, self.manholes)
            roads_path = rd.RAW / "hyderabad_roads.geojson"
            self.roads = rd.load_roads(self.bounds) if roads_path.is_file() else sc.generate_roads(self.bounds)
            self.historical_flood = sc.generate_historical_inundation(self.bounds)
            self.dem, self.dem_transform = sc.generate_dem(self.bounds, resolution=60)
            self.landcover_classes, self.imperviousness = sc.generate_landcover(self.bounds, resolution=60)

        self.road_source = str(self.roads.iloc[0].get("source", "synthetic-demo"))
        self.dem_source = str(self.dem_transform.get("source", "synthetic-demo"))
        self.drainage_source = "synthetic-demo"
        self.roads_m = self.roads.to_crs(self.bounds.projected_crs)
        self.road_graph = build_road_graph(self.roads)

        filled = dp.fill_sinks(self.dem)
        self.flow_dir = dp.d8_flow_direction(filled)
        self.flow_acc = dp.flow_accumulation(self.flow_dir, filled)
        self.low_points_rc = dp.identify_low_points(self.dem, self.flow_acc, top_k=6)
        self.low_points_lonlat = [dp.grid_to_lonlat(r, c, self.dem_transform) for r, c in self.low_points_rc]

        self.graph = build_drainage_graph(self.manholes, self.sewerlines, self.nala, self.connectors)
        add_inferred_edges_from_low_points(self.graph, self.low_points_lonlat)

        # cell area for the DEM grid (approx, meters)
        res = self.dem_transform["resolution"]
        width_m = (self.bounds.max_lon - self.bounds.min_lon) * 111_320 * np.cos(
            np.radians((self.bounds.min_lat + self.bounds.max_lat) / 2))
        height_m = (self.bounds.max_lat - self.bounds.min_lat) * 110_540
        self.cell_area_m2 = (width_m / res) * (height_m / res)

        # Keep generated synthetic analysis frames project-relative as well.
        self.cctv_frames_dir = str(Path(__file__).resolve().parents[3] / "data" / "demo" / "cctv_frames")
        self.cctv_frames = generate_demo_frames(self.cctv_frames_dir)

        # static per-node ponding-area estimate (contributing area based),
        # computed once and reused across every simulation run
        self.ponding_areas = compute_node_ponding_areas(
            self.graph, self.dem_transform, self.cell_area_m2)

        # Boundary condition: nodes with no outgoing edge that are the
        # catchment's drainage EXIT (trunk-nala outfall, or the last node
        # of the trunk chain) represent water leaving the modeled domain
        # (into the Musi River / downstream of the catchment), not a real
        # flood point -- we have no river stage data to model backwater
        # here, so this is a documented free-draining boundary assumption
        # (see docs/limitations.md), not a claim about actual river
        # capacity.
        for node_id in self.graph.nodes:
            if self.graph.out_degree(node_id) == 0 and (
                    str(node_id).startswith("OUTFALL-") or str(node_id).startswith("SYN-NALA-NODE-")):
                self.ponding_areas[node_id] = 1e9

    def node_lonlat_lookup(self) -> dict:
        lookup = {}
        for node_id, attrs in self.graph.nodes(data=True):
            geom = attrs.get("geometry")
            if geom is None:
                continue
            lonlat = (geom.x, geom.y) if hasattr(geom, "x") else tuple(geom)
            lookup[lonlat] = node_id
        return lookup

    def cn_raster(self):
        return imperviousness_to_cn(self.imperviousness)


_catchment_singleton: Catchment | None = None


def get_catchment() -> Catchment:
    global _catchment_singleton
    if _catchment_singleton is None:
        _catchment_singleton = Catchment()
    return _catchment_singleton


def _rainfall_to_node_inflow_series(catchment: Catchment, rainfall_df) -> list[dict]:
    cn = catchment.cn_raster()
    hyetograph = rainfall_df["rainfall_mm"].to_numpy()
    runoff_series = runoff_timeseries(hyetograph, cn)  # (T, rows, cols)
    inflow_series = []
    for t in range(runoff_series.shape[0]):
        inflow = assign_runoff_to_nearest_node(
            runoff_series[t], catchment.dem_transform, catchment.graph, catchment.cell_area_m2)
        inflow_series.append(inflow)
    return inflow_series


def _capacity_utilization_series(timestep_log: list[dict]) -> dict:
    times = np.array([0] + [(entry["t_index"] + 1) * 60 for entry in timestep_log], dtype=float)
    node_ids = set().union(*(entry["capacity_utilization_pct"] for entry in timestep_log))
    result = {}
    for node_id in node_ids:
        values = np.array([0.0] + [
            entry["capacity_utilization_pct"].get(node_id, 0.0)
            for entry in timestep_log
        ], dtype=float)
        result[node_id] = {
            minute: float(np.interp(minute, times, values))
            if minute <= times[-1] else float(values[-1])
            for minute in NOWCAST_MINUTES
        }
    return result


def _nearest_road(catchment: Catchment, lon: float, lat: float) -> str | None:
    if catchment.roads.empty:
        return None
    transformer = Transformer.from_crs("EPSG:4326", catchment.bounds.projected_crs, always_xy=True)
    point_m = Point(*transformer.transform(lon, lat))
    return str(catchment.roads_m.loc[
        catchment.roads_m.geometry.distance(point_m).idxmin(), "road_id"
    ])


def _build_horizon_products(catchment: Catchment, nowcast: list[dict], observations: list[dict]) -> list[dict]:
    road_graph = catchment.road_graph.copy()
    road_rank = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3, "CRITICAL": 4}
    drainage_points = []
    for entry in nowcast:
        attrs = catchment.graph.nodes.get(entry["node_id"], {})
        geom = attrs.get("geometry")
        if geom is None:
            continue
        lon, lat = (geom.x, geom.y) if hasattr(geom, "x") else tuple(geom)
        drainage_points.append((entry["node_id"], Point(lon, lat)))
    from shapely.strtree import STRtree
    drainage_geometries = [geometry for _node_id, geometry in drainage_points]
    drainage_tree = STRtree(drainage_geometries) if drainage_geometries else None
    cctv_risk_by_road = {
        observation["road_id"]: "HIGH"
        for observation in observations
        if observation.get("flood_detected") and observation.get("road_id")
    }
    outputs = []

    for minute in NOWCAST_MINUTES:
        node_risks = {}
        drainage = []
        hotspots = []
        severity = "NONE"
        for entry in nowcast:
            attrs = catchment.graph.nodes.get(entry["node_id"], {})
            geom = attrs.get("geometry")
            if geom is None:
                continue
            lon, lat = (geom.x, geom.y) if hasattr(geom, "x") else tuple(geom)
            forecast = next(item for item in entry["forecast"] if item["minute"] == minute)
            node_risks[(lon, lat)] = forecast["risk"]
            drainage_item = {
                "node_id": entry["node_id"], "lon": lon, "lat": lat,
                "source": attrs.get("source", "synthetic-demo"),
                "depth_m": forecast["predicted_depth_m"],
                "risk": forecast["risk"],
                "capacity_utilization_pct": forecast["capacity_utilization_pct"],
            }
            drainage.append(drainage_item)
            if road_rank[forecast["risk"]] > road_rank[severity]:
                severity = forecast["risk"]
            if forecast["risk"] != "NONE":
                hotspots.append({
                    "id": f"HS-{entry['node_id']}", "node_id": entry["node_id"],
                    "lon": lon, "lat": lat, "depth_m": forecast["predicted_depth_m"],
                    "risk": forecast["risk"], "source": attrs.get("source", "synthetic-demo"),
                })

        annotate_edge_risk(road_graph, node_risks)
        road_risk = {}
        for _u, _v, attrs in road_graph.edges(data=True):
            road_id = str(attrs["road_id"])
            candidate = attrs.get("risk", "NONE")
            if road_rank[cctv_risk_by_road.get(road_id, "NONE")] > road_rank[candidate]:
                candidate = cctv_risk_by_road[road_id]
            if road_rank[candidate] > road_rank.get(road_risk.get(road_id, "NONE"), 0):
                road_risk[road_id] = candidate

        roads = []
        drainage_by_node = {item["node_id"]: item for item in drainage}
        for _, road in catchment.roads.iterrows():
            road_id = str(road["road_id"])
            risk = road_risk.get(road_id, cctv_risk_by_road.get(road_id, "NONE"))
            nearby_indexes = drainage_tree.query(road.geometry.buffer(0.0005)) if drainage_tree else []
            nearby = [drainage_by_node[drainage_points[int(index)][0]] for index in nearby_indexes]
            depth_m = max((item["depth_m"] for item in nearby), default=0.0)
            roads.append({
                "road_id": road_id, "road_name": road.get("road_name", ""),
                "road_class": road.get("road_class", "unknown"),
                "source": road.get("source", "synthetic-demo"),
                "risk": risk, "depth_m": depth_m,
                "status": "CLOSED" if risk == "CRITICAL" else "RESTRICTED" if risk in ("HIGH", "CRITICAL") else "OPEN",
                "cctv_confirmed": road_id in cctv_risk_by_road,
            })

        outputs.append({
            "minute": minute, "severity": severity,
            "hotspots": hotspots, "drainage": drainage, "roads": roads,
        })
    return outputs


def run_scenario(catchment: Catchment, rainfall_mode: str = "demo",
                 dispatch_notifications: bool = True) -> dict:
    """Run one backend-owned forecast using real observations or explicit demo rainfall."""
    import time

    t0 = time.perf_counter()
    if rainfall_mode == "real":
        rainfall_df, rainfall_source = rd.load_observed_rainfall(catchment.bounds)
        rainfall_df = rainfall_df.tail(8).reset_index(drop=True)
        if rainfall_df.empty:
            raise FileNotFoundError("No usable catchment rainfall observations were found")
    elif rainfall_mode == "demo":
        rainfall_df = sc.generate_rainfall_event(event_name="live-demo-storm", total_mm=150.0, hours=4)
        rainfall_source = "synthetic-demo"
    else:
        raise ValueError("rainfall_mode must be 'real' or 'demo'")
    inflow_series = _rainfall_to_node_inflow_series(catchment, rainfall_df)
    state = run_simulation(catchment.graph, inflow_series, ponding_area_m2=catchment.ponding_areas)
    node_series = interpolate_depth_series(state.timestep_log)
    nowcast = build_nowcast_response(node_series, node_utilization_series=_capacity_utilization_series(state.timestep_log))

    # CCTV clips are samples; associate each observation with the nearest
    # current road feature so detections can influence route risk.
    observations = []
    node_lookup = catchment.node_lonlat_lookup()
    node_coords = np.array(list(node_lookup.keys()))
    node_ids = list(node_lookup.values())
    assimilated = []
    cameras = []
    for index, frame_meta in enumerate(catchment.cctv_frames, start=1):
        camera_id = f"CAM-{index:03d}"
        img = cv2.imread(frame_meta["frame_path"])
        obs = detect_flood(img, camera_id, frame_meta["lat"], frame_meta["lon"],
                             timestamp="T+60min")
        road_id = _nearest_road(catchment, obs.lon, obs.lat)
        observation = {**obs.__dict__, "road_id": road_id, "source": "synthetic-demo"}
        observations.append(observation)
        cameras.append({
            "camera_id": camera_id, "lat": obs.lat, "lon": obs.lon,
            "road_id": road_id, "location": f"Sample camera near {road_id or 'unmapped road'}",
            "video_url": f"/api/cctv/{camera_id}/video", "source": "synthetic-demo",
            "status": "WATERLOGGING" if obs.flood_detected else "NORMAL",
            "depth_m": obs.estimated_depth_m or 0.0,
            "confidence": round(obs.confidence * 100),
        })
        obs.camera_id = camera_id
        if obs.flood_detected:
            d2 = (node_coords[:, 0] - obs.lon) ** 2 + (node_coords[:, 1] - obs.lat) ** 2
            nearest_node = node_ids[int(np.argmin(d2))]
            predicted_depth = node_series.get(nearest_node, {}).get(60, 0.0)
            result = cctv_mod.assimilate(predicted_depth, obs)
            result["node_id"] = nearest_node
            result["camera_id"] = camera_id
            result["road_id"] = road_id
            assimilated.append(result)

    for entry in nowcast:
        attrs = catchment.graph.nodes.get(entry["node_id"], {})
        geom = attrs.get("geometry")
        if geom is not None:
            lon, lat = (geom.x, geom.y) if hasattr(geom, "x") else tuple(geom)
            entry["location"] = [lon, lat]
            entry["source"] = attrs.get("source", "synthetic-demo")

    horizons = _build_horizon_products(catchment, nowcast, observations)
    threshold_alerts = []
    risk_rank = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3, "CRITICAL": 4}
    triggering_horizon = next((h for h in horizons if risk_rank[h["severity"]] >= 3), None)
    if triggering_horizon:
        alert = AlertContent(
            locality=catchment.bounds.name,
            expected_depth_m=max((item["depth_m"] for item in triggering_horizon["hotspots"]), default=0.0),
            expected_time_minute=triggering_horizon["minute"],
            severity=triggering_horizon["severity"],
            alt_route_summary="avoid high-risk roads; use the flood-aware route",
        )
        if dispatch_notifications:
            delivery = dispatch_alert(alert, channel="webhook")
        else:
            from app.alerts.alert_service import render_alert
            delivery = {
                "channel": "webhook", "dispatched": False,
                "mode": "NOT_DISPATCHED (routing request)",
                "messages": {lang: render_alert(alert, lang) for lang in ("en", "te")},
            }
        threshold_alerts.append({
            "trigger": "backend_flood_threshold", "minute": triggering_horizon["minute"],
            "severity": triggering_horizon["severity"],
            "messages": delivery["messages"], "dispatch": delivery,
        })

    runtime_seconds = time.perf_counter() - t0
    return {
        "rainfall_event": rainfall_df.to_dict(orient="records"),
        "rainfall_source": rainfall_source,
        "rainfall_mode": rainfall_mode,
        "nowcast": nowcast,
        "horizons": horizons,
        "cctv_observations": observations,
        "cctv_cameras": cameras,
        "assimilation_results": assimilated,
        "flood_probes": [f["frame_path"] for f in catchment.cctv_frames],
        "alerts": threshold_alerts,
        "data_source": "REAL" if rainfall_source.startswith("real:") else "SYNTHETIC",
        "road_source": catchment.road_source,
        "drainage_source": catchment.drainage_source,
        "runtime_seconds": round(float(runtime_seconds), 3),
        "mode": "DEMO" if rainfall_mode == "demo" else "REAL_RAINFALL",
        "note": ("Observed rainfall drives the prototype simulation; IMERG is not a future rainfall forecast. "
             if rainfall_mode == "real" else "Explicit synthetic rainfall fallback/demo mode. ")
            + "Road, drainage, inferred DEM, and CCTV provenance are provided per layer.",
    }


def run_historical_replay(catchment: Catchment) -> dict:
    """Spec section 12, 'Historical Event Replay': rainfall -> prediction ->
    observation -> calibration -> improved prediction, for the 13 Oct 2020
    profile."""
    import time

    t0 = time.perf_counter()
    rainfall_df = sc.generate_rainfall_event(event_name="2020-10-13", total_mm=192.0, hours=6)
    inflow_series = _rainfall_to_node_inflow_series(catchment, rainfall_df)

    calibration_result = calibrate(catchment.graph, inflow_series, catchment.historical_flood,
                                     ponding_area_m2=catchment.ponding_areas)

    # holdout transfer check (event-based validation, spec section 6)
    holdout_rainfall = build_holdout_event(catchment.bounds)
    holdout_inflow = _rainfall_to_node_inflow_series(catchment, holdout_rainfall)
    holdout_flood = sc.generate_historical_inundation(catchment.bounds)  # reuse generator, different seed context
    transfer_result = validate_transfer(catchment.graph, holdout_inflow, holdout_flood,
                                          calibration_result["after_calibration"]["factors"],
                                          ponding_area_m2=catchment.ponding_areas)

    return {
        "rainfall_event": rainfall_df.to_dict(orient="records"),
        "calibration": calibration_result,
        "holdout_validation": transfer_result,
        "historical_flood_polygons_geojson": catchment.historical_flood.to_json(),
        "data_source": "SYNTHETIC",
        "mode": "HISTORICAL_REPLAY",
        "runtime_seconds": round(float(time.perf_counter() - t0), 3),
        "note": "HISTORICAL EVENT REPLAY: using the 13 Oct 2020 Hyderabad profile with synthetic spatial labels for calibration validation.",
    }
