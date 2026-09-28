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
from app.calibration.inverse_calibration import calibrate
from app.calibration.event_validation import build_holdout_event, validate_transfer
from app.cctv.synthetic_frames import generate_demo_frames
from app.cctv.flood_detection import detect_flood
from app.cctv import flood_detection as cctv_mod
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
            self.roads = sc.generate_roads(self.bounds)
            self.historical_flood = sc.generate_historical_inundation(self.bounds)
            self.dem, self.dem_transform = sc.generate_dem(self.bounds, resolution=60)
            self.landcover_classes, self.imperviousness = sc.generate_landcover(self.bounds, resolution=60)

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


def run_scenario(catchment: Catchment) -> dict:
    """Spec section 12, 'Run Flood Scenario' — deterministic synthetic
    storm demonstrating the full pipeline through CCTV assimilation and
    routing/alerting."""
    rainfall_df = sc.generate_rainfall_event(event_name="live-demo-storm", total_mm=150.0, hours=4)
    inflow_series = _rainfall_to_node_inflow_series(catchment, rainfall_df)
    state = run_simulation(catchment.graph, inflow_series, ponding_area_m2=catchment.ponding_areas)
    node_series = interpolate_depth_series(state.timestep_log)
    nowcast = build_nowcast_response(node_series)

    # CCTV: run detector on the synthetic frames, assimilate the worst one
    # against whichever graph node is nearest that camera
    observations = []
    node_lookup = catchment.node_lonlat_lookup()
    node_coords = np.array(list(node_lookup.keys()))
    node_ids = list(node_lookup.values())
    assimilated = []
    for frame_meta in catchment.cctv_frames:
        img = cv2.imread(frame_meta["frame_path"])
        obs = detect_flood(img, frame_meta["camera_id"], frame_meta["lat"], frame_meta["lon"],
                             timestamp="T+60min")
        observations.append(obs.__dict__)
        if obs.flood_detected:
            d2 = (node_coords[:, 0] - obs.lon) ** 2 + (node_coords[:, 1] - obs.lat) ** 2
            nearest_node = node_ids[int(np.argmin(d2))]
            predicted_depth = node_series.get(nearest_node, {}).get(60, 0.0)
            result = cctv_mod.assimilate(predicted_depth, obs)
            result["node_id"] = nearest_node
            result["camera_id"] = obs.camera_id
            assimilated.append(result)

    return {
        "rainfall_event": rainfall_df.to_dict(orient="records"),
        "nowcast": nowcast,
        "cctv_observations": observations,
        "assimilation_results": assimilated,
        "flood_probes": [f["frame_path"] for f in catchment.cctv_frames],
        "note": "Demo storm is synthetic-demo rainfall over the synthetic-demo "
                "catchment. See data_inventory.md.",
    }


def run_historical_replay(catchment: Catchment) -> dict:
    """Spec section 12, 'Historical Event Replay': rainfall -> prediction ->
    observation -> calibration -> improved prediction, for the 13 Oct 2020
    profile."""
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
    }
