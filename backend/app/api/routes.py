from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from starlette.responses import FileResponse, StreamingResponse
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
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"node_id": node_id, "kind": attrs.get("kind"),
                             "source": attrs.get("source"), "confidence": attrs.get("confidence"),
                             "label": f"Synthetic Catchment Zone {(index % 4) + 1}",
                             "upstream": inbound, "downstream": outbound[0] if outbound else "NALA-OUTFALL",
                             "connected_roads": [f"R-{((index * 3) % 50) + 1:03d}", f"R-{((index * 3 + 7) % 50) + 1:03d}"],
                             "capacity": 58 + (index * 7) % 38,
                             "blockage": 8 + (index * 9) % 42},
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
        "cctv_cameras": [
            {"camera_id": "CAM-001", "lat": 17.3694, "lon": 78.4589, "road_id": "R-002", "location": "Northwest gateway", "video_url": "/api/cctv/CAM-001/video"},
            {"camera_id": "CAM-002", "lat": 17.3731, "lon": 78.4612, "road_id": "R-010", "location": "Collector junction", "video_url": "/api/cctv/CAM-002/video"},
            {"camera_id": "CAM-003", "lat": 17.3741, "lon": 78.4697, "road_id": "R-017", "location": "Central drainage crossing", "video_url": "/api/cctv/CAM-003/video"},
            {"camera_id": "CAM-004", "lat": 17.3758, "lon": 78.4750, "road_id": "R-027", "location": "Eastern low point", "video_url": "/api/cctv/CAM-004/video"},
            {"camera_id": "CAM-005", "lat": 17.3717, "lon": 78.4790, "road_id": "R-036", "location": "Nala outfall approach", "video_url": "/api/cctv/CAM-005/video"},
        ],
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
    # Send the snapped network points back to the dashboard. The frontend
    # can show users which graph locations their map picks resolved to.
    result["selected_start"] = {"node": [start[0], start[1]], "lat": start[1], "lon": start[0]}
    result["selected_end"] = {"node": [end[0], end[1]], "lat": end[1], "lon": end[0]}
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
