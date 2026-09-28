from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from starlette.responses import FileResponse, StreamingResponse
from shapely.geometry import mapping
import os

from app.demo.orchestrator import get_catchment, run_scenario, run_historical_replay
from app.routing.flood_aware_routing import build_road_graph, annotate_edge_risk, compute_routes
from app.alerts.alert_service import AlertContent, dispatch_alert

router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CCTV_VIDEOS = {
    "CAM-001": "cam_001_normal.mp4",
    "CAM-002": "cam_002_light_flood.mp4",
    "CAM-003": "cam_003_waterlogging.mp4",
    "CAM-004": "cam_004_severe_flood.mp4",
    "CAM-005": "cam_005_blockage.mp4",
}


def _stream_video_segment(path: Path, start: int, end: int, chunk_size: int = 1024 * 256):
    with path.open("rb") as video:
        video.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = video.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/cctv/{camera_id}/video")
def get_cctv_video(camera_id: str, range_header: str | None = Header(default=None, alias="Range")):
    """Serve synthetic CCTV clips with byte-range support for HTML5 video."""
    filename = CCTV_VIDEOS.get(camera_id.upper())
    if not filename:
        raise HTTPException(status_code=404, detail="Unknown synthetic camera")
    path = PROJECT_ROOT / "data" / "synthetic_cctv" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Synthetic CCTV clip is unavailable")

    file_size = path.stat().st_size
    if not range_header:
        return FileResponse(path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})

    try:
        unit, requested_range = range_header.split("=", 1)
        start_text, end_text = requested_range.split("-", 1)
        if unit != "bytes":
            raise ValueError
        start = int(start_text) if start_text else 0
        end = int(end_text) if end_text else file_size - 1
        if start < 0 or end < start or start >= file_size:
            raise ValueError
        end = min(end, file_size - 1)
    except ValueError:
        raise HTTPException(status_code=416, detail="Invalid video byte range")

    content_length = end - start + 1
    return StreamingResponse(
        _stream_video_segment(path, start, end),
        status_code=206,
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(content_length),
        },
    )


def _node_geojson(catchment) -> dict:
    features = []
    for node_id, attrs in catchment.graph.nodes(data=True):
        geom = attrs.get("geometry")
        if geom is None:
            continue
        lon, lat = (geom.x, geom.y) if hasattr(geom, "x") else tuple(geom)
        inbound = [str(u) for u, _v in catchment.graph.in_edges(node_id)]
        outbound = [str(v) for _u, v in catchment.graph.out_edges(node_id)]
        index = len(features) + 1
        inferred = str(attrs.get("source", "")).startswith("inferred:")
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"node_id": node_id, "kind": attrs.get("kind"),
                             "source": attrs.get("source"), "confidence": attrs.get("confidence"),
                             "label": f"{('Inferred' if inferred else 'Catchment')} node {index:03d}",
                             "upstream": inbound, "downstream": outbound[0] if outbound else None},
        })
    return {"type": "FeatureCollection", "features": features}


def _inferred_flowpaths_geojson(catchment) -> dict:
    features = []
    for source, target, attrs in catchment.graph.edges(data=True):
        if not str(attrs.get("source", "")).startswith("inferred:"):
            continue
        geometry = attrs.get("geometry")
        if geometry is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": mapping(geometry),
            "properties": {"from_node": str(source), "to_node": str(target),
                           "source": attrs.get("source"), "confidence": attrs.get("confidence"),
                           "edge_kind": attrs.get("edge_kind")},
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
        "sources": {"roads": c.road_source, "drainage": c.drainage_source,
                "dem": c.dem_source},
        "manholes": _node_geojson(c),
        "sewerlines": _edge_geojson(c.sewerlines, "piped_sewer"),
        "nala": _edge_geojson(c.nala, "open_nala"),
        "inferred_flowpaths": _inferred_flowpaths_geojson(c),
        "roads": _edge_geojson(c.roads, "roads"),
        "historical_flood": _edge_geojson(c.historical_flood, "historical_inundation"),
        "cctv_cameras": [],
    }


@router.get("/catchment/dem")
def get_dem():
    c = get_catchment()
    return {"dem": c.dem.tolist(), "transform": c.dem_transform,
            "flow_accumulation": c.flow_acc.tolist(),
            "low_points_lonlat": c.low_points_lonlat}


@router.get("/scenario/run")
def scenario_run(rainfall_mode: str | None = None):
    c = get_catchment()
    rainfall_mode = rainfall_mode or os.getenv("HYDROLOOP_RAINFALL_MODE", "demo")
    if rainfall_mode not in ("real", "demo"):
        raise HTTPException(status_code=400, detail="rainfall_mode must be 'real' or 'demo'")
    try:
        return run_scenario(c, rainfall_mode=rainfall_mode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    rainfall_mode: str | None = None


@router.post("/routing/route")
def route(req: RouteRequest):
    c = get_catchment()
    road_graph = c.road_graph.copy()

    def nearest_node(lon, lat):
        return min(road_graph.nodes, key=lambda n: (n[0] - lon) ** 2 + (n[1] - lat) ** 2)

    start = nearest_node(req.start_lon, req.start_lat)
    end = nearest_node(req.end_lon, req.end_lat)

    if req.horizon_minute not in (0, 15, 30, 60, 120, 180):
        raise HTTPException(status_code=400, detail="horizon_minute must be one of 0, 15, 30, 60, 120, 180")

    # Road risk comes from the same selected backend scenario product used by
    # the dashboard; road IDs remain those of the active catchment layer.
    rainfall_mode = req.rainfall_mode or os.getenv("HYDROLOOP_RAINFALL_MODE", "demo")
    if rainfall_mode not in ("real", "demo"):
        raise HTTPException(status_code=400, detail="rainfall_mode must be 'real' or 'demo'")
    try:
        scenario = run_scenario(c, rainfall_mode=rainfall_mode, dispatch_notifications=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    selected_horizon = next(h for h in scenario["horizons"] if h["minute"] == req.horizon_minute)
    risk_by_road = {road["road_id"]: road["risk"] for road in selected_horizon["roads"]}
    for _u, _v, attrs in road_graph.edges(data=True):
        attrs["risk"] = risk_by_road.get(str(attrs["road_id"]), attrs.get("risk", "NONE"))
    result = compute_routes(road_graph, start, end, profile=req.profile)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    result["horizon_minute"] = req.horizon_minute
    # Send the snapped network points back to the dashboard. The frontend
    # can show users which graph locations their map picks resolved to.
    result["selected_start"] = {"node": [start[0], start[1]], "lat": start[1], "lon": start[0]}
    result["selected_end"] = {"node": [end[0], end[1]], "lat": end[1], "lon": end[0]}
    result["data_status"] = c.data_mode
    result["rainfall_source"] = scenario["rainfall_source"]
    result["road_source"] = c.road_source
    result["dem_source"] = c.dem_source
    result["drainage_source"] = c.drainage_source
    result["data_note"] = (
        f"Roads: {c.road_source}; DEM: {c.dem_source}; "
        f"drainage: {c.drainage_source}; rainfall: {scenario['rainfall_source']}."
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







