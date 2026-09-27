from __future__ import annotations
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os

from app.demo.orchestrator import get_catchment, run_scenario, run_historical_replay
from app.routing.flood_aware_routing import build_road_graph, annotate_edge_risk, compute_routes
from app.alerts.alert_service import AlertContent, dispatch_alert

router = APIRouter()


def _node_geojson(catchment) -> dict:
    features = []
    for node_id, attrs in catchment.graph.nodes(data=True):
        geom = attrs.get("geometry")
        if geom is None:
            continue
        lon, lat = (geom.x, geom.y) if hasattr(geom, "x") else tuple(geom)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"node_id": node_id, "kind": attrs.get("kind"),
                             "source": attrs.get("source"), "confidence": attrs.get("confidence")},
        })
    return {"type": "FeatureCollection", "features": features}


def _edge_geojson(gdf, layer_name: str) -> dict:
    return json.loads(gdf.to_json()) | {"layer": layer_name}


@router.get("/catchment/layers")
def get_layers():
    """All GIS layers for the map (manholes, sewer, nala, roads, historical
    flood polygons), each tagged with source/confidence so the frontend can
    render real vs inferred/synthetic distinctly (spec section 3/11)."""
    c = get_catchment()
    return {
        "bounds": c.bounds.__dict__,
        "manholes": _node_geojson(c),
        "sewerlines": _edge_geojson(c.sewerlines, "piped_sewer"),
        "nala": _edge_geojson(c.nala, "open_nala"),
        "roads": _edge_geojson(c.roads, "roads"),
        "historical_flood": _edge_geojson(c.historical_flood, "historical_inundation"),
        "cctv_cameras": [{"camera_id": f["camera_id"], "lat": f["lat"], "lon": f["lon"]}
                          for f in c.cctv_frames],
    }


@router.get("/catchment/dem")
def get_dem():
    c = get_catchment()
    return {"dem": c.dem.tolist(), "transform": c.dem_transform,
            "flow_accumulation": c.flow_acc.tolist(),
            "low_points_lonlat": c.low_points_lonlat}


@router.get("/scenario/run")
def scenario_run():
    c = get_catchment()
    return run_scenario(c)


@router.get("/scenario/historical-replay")
def scenario_historical_replay():
    c = get_catchment()
    return run_historical_replay(c)


class RouteRequest(BaseModel):
    start_lon: float
    start_lat: float
    end_lon: float
    end_lat: float
    profile: str = "NORMAL"
    horizon_minute: int = 60


@router.post("/routing/route")
def route(req: RouteRequest):
    c = get_catchment()
    road_graph = build_road_graph(c.roads)

    def nearest_node(lon, lat):
        return min(road_graph.nodes, key=lambda n: (n[0] - lon) ** 2 + (n[1] - lat) ** 2)

    start = nearest_node(req.start_lon, req.start_lat)
    end = nearest_node(req.end_lon, req.end_lat)

    if req.horizon_minute not in (0, 15, 30, 60, 120, 180):
        raise HTTPException(status_code=400, detail="horizon_minute must be one of 0, 15, 30, 60, 120, 180")

    # The current demo scenario is synthetic, but the routing contract is
    # intentionally data-source agnostic. Once real rainfall/DEM/road data
    # is loaded by the catchment adapter, this same endpoint can consume it.
    scenario = run_scenario(c)
    node_risk = {}
    for entry in scenario["nowcast"]:
        node_id = entry["node_id"]
        attrs = c.graph.nodes.get(node_id, {})
        geom = attrs.get("geometry")
        if geom is None:
            continue
        lonlat = (geom.x, geom.y) if hasattr(geom, "x") else tuple(geom)
        risk_at_horizon = next(
            (f["risk"] for f in entry["forecast"] if f["minute"] == req.horizon_minute),
            "NONE",
        )
        node_risk[lonlat] = risk_at_horizon

    # Keep the spatial association tight: a flood-model node should only
    # affect nearby road segments, not the entire surrounding grid.
    annotate_edge_risk(road_graph, node_risk, risk_radius_deg=0.0005)
    result = compute_routes(road_graph, start, end, profile=req.profile)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    result["horizon_minute"] = req.horizon_minute
    result["data_status"] = c.data_mode
    if c.data_mode == "real":
        result["data_note"] = (
            "REAL road network + REAL DEM are active. "
            "Drainage/rainfall calibration still requires verified real layers."
        )
    else:
        result["data_note"] = (
            "Synthetic demo mode. Set HYDROLOOP_DATA_MODE=real only after "
            "real DEM and road files have been downloaded and verified."
        )
    return result


class AlertRequest(BaseModel):
    locality: str
    expected_depth_m: float
    expected_time_minute: int
    severity: str
    alt_route_summary: str = "see flood-aware route"


@router.post("/alerts/dispatch")
def alerts_dispatch(req: AlertRequest):
    content = AlertContent(**req.model_dump())
    return dispatch_alert(content)


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/data/status")
def data_status():
    from app.ingestion.real_data import data_status as _status
    return _status()
