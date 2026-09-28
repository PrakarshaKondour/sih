"""
Flood-aware routing (spec section 9). Builds a routable graph from the
road GeoDataFrame, then computes shortest path with edge weights penalized
by nearby flood risk, vs. a plain-distance "normal" route, for three
profiles: NORMAL, AMBULANCE, FIRE/EMERGENCY (emergency profiles are more
willing to accept a moderate-risk shortcut than reroute far around it,
reflecting real triage trade-offs — this is a documented policy choice,
not a validated operations-research result).
"""
from __future__ import annotations
import networkx as nx
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, LineString

RISK_PENALTY_MULTIPLIER = {
    "NONE": 1.0, "LOW": 1.3, "MODERATE": 2.5, "HIGH": 8.0, "CRITICAL": 1e6,  # effectively impassable
}

PROFILE_RISK_TOLERANCE = {
    # multiplies the penalty down for emergency profiles willing to accept
    # more risk to save time, EXCEPT critical (never route through critical)
    "NORMAL": 1.0,
    "AMBULANCE": 0.6,
    "FIRE": 0.5,
}


def build_road_graph(roads: gpd.GeoDataFrame) -> nx.Graph:
    """Builds a routable planar graph from the road GeoDataFrame.

    Road features are independent LineStrings (as a real roads layer would
    give you) with no shared vertices at intersections, so this explicitly
    uses a spatial index to find intersecting road lines and inserts those
    intersections as graph nodes before adding edges."""
    from shapely.geometry import Point as _Point
    from shapely.strtree import STRtree

    lines = list(roads.geometry)
    road_ids = list(roads["road_id"])
    road_classes = list(roads["road_class"])
    tree = STRtree(lines)
    cut_points_by_line = [list(line.coords) for line in lines]

    def intersection_points(geometry):
        if geometry.geom_type == "Point":
            return [tuple(geometry.coords[0])]
        if geometry.geom_type in ("LineString", "LinearRing"):
            return [tuple(geometry.coords[0]), tuple(geometry.coords[-1])]
        if hasattr(geometry, "geoms"):
            return [point for part in geometry.geoms for point in intersection_points(part)]
        return []

    for i, line_i in enumerate(lines):
        for j in tree.query(line_i, predicate="intersects"):
            j = int(j)
            if j <= i:
                continue
            points = intersection_points(line_i.intersection(lines[j]))
            cut_points_by_line[i].extend(points)
            cut_points_by_line[j].extend(points)

    g = nx.Graph()
    for i, line_i in enumerate(lines):
        cut_points = cut_points_by_line[i]
        cut_points = sorted(set(cut_points), key=lambda p: line_i.project(_Point(p)))
        for a, b in zip(cut_points[:-1], cut_points[1:]):
            if a == b:
                continue
            dist_m = _dist_m(a, b)
            if dist_m <= 0:
                continue
            g.add_edge(a, b, base_length_m=dist_m, road_id=road_ids[i], road_class=road_classes[i])
    return g


def _dist_m(a, b) -> float:
    dx = (b[0] - a[0]) * 111_320 * np.cos(np.radians((a[1] + b[1]) / 2))
    dy = (b[1] - a[1]) * 110_540
    return float((dx ** 2 + dy ** 2) ** 0.5)


def annotate_edge_risk(g: nx.Graph, node_risk_lookup: dict, risk_radius_deg: float = 0.0005):
    """node_risk_lookup: {(lon,lat): risk_str} for flood-model nodes.
    For each road edge, find the max risk among nearby flood nodes and
    store it as edge attribute 'risk'."""
    from shapely.strtree import STRtree

    rank = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3, "CRITICAL": 4}
    risk_locations = list(node_risk_lookup)
    risk_geometries = [Point(lon, lat) for lon, lat in risk_locations]
    risk_values = [node_risk_lookup[location] for location in risk_locations]
    risk_tree = STRtree(risk_geometries) if risk_geometries else None
    for u, v, data in g.edges(data=True):
        edge_line = LineString([u, v])
        worst = "NONE"
        worst_rank = 0
        candidates = risk_tree.query(edge_line.buffer(risk_radius_deg)) if risk_tree else []
        for index in candidates:
            risk = risk_values[int(index)]
            if rank.get(risk, 0) > worst_rank:
                worst, worst_rank = risk, rank.get(risk, 0)
        data["risk"] = worst


def weighted_length(g: nx.Graph, profile: str = "NORMAL"):
    tolerance = PROFILE_RISK_TOLERANCE.get(profile, 1.0)
    for u, v, data in g.edges(data=True):
        risk = data.get("risk", "NONE")
        penalty = RISK_PENALTY_MULTIPLIER[risk]
        if risk != "CRITICAL":
            penalty = 1.0 + (penalty - 1.0) * tolerance
        data["weighted_length_m"] = data["base_length_m"] * penalty


def _path_details(g: nx.Graph, path) -> tuple[list[str], list[dict]]:
    road_ids = []
    segments = []
    for u, v in zip(path[:-1], path[1:]):
        data = g[u][v]
        road_id = str(data.get("road_id", "UNKNOWN"))
        if road_id not in road_ids:
            road_ids.append(road_id)
        segments.append({
            "from": [u[0], u[1]],
            "to": [v[0], v[1]],
            "road_id": road_id,
            "road_class": data.get("road_class"),
            "risk": data.get("risk", "NONE"),
            "length_m": round(float(data.get("base_length_m", 0)), 1),
        })
    return road_ids, segments


def compute_routes(g: nx.Graph, start, end, profile: str = "NORMAL") -> dict:
    """Return a normal route and a flood-aware alternative.

    Critical edges are treated as blocked for the flood-aware route. If a
    completely safe path does not exist, the weighted-risk path is attempted
    as a fallback. This makes the demo deterministic and prevents the
    navigation layer from silently recommending a critical road when a safe
    graph path exists.
    """
    weighted_length(g, profile)
    try:
        normal_path = nx.shortest_path(g, start, end, weight="base_length_m")
        normal_len = nx.shortest_path_length(g, start, end, weight="base_length_m")
    except nx.NetworkXNoPath:
        return {"error": "no path found in demo road graph"}

    risk_rank = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3, "CRITICAL": 4}

    # Build a copy with CRITICAL road segments removed. This is the actual
    # "avoid flooded road" navigation graph.
    safe_graph = g.copy()
    critical_edges = [
        (u, v) for u, v, data in safe_graph.edges(data=True)
        if data.get("risk", "NONE") == "CRITICAL"
    ]
    safe_graph.remove_edges_from(critical_edges)

    try:
        aware_path = nx.shortest_path(safe_graph, start, end, weight="weighted_length_m")
        aware_len = sum(
            g[u][v]["base_length_m"] for u, v in zip(aware_path[:-1], aware_path[1:])
        )
        safe_path_found = True
    except nx.NetworkXNoPath:
        # If the critical-road-free graph is disconnected, use the weighted
        # graph as a fallback rather than pretending an alternate exists.
        try:
            aware_path = nx.shortest_path(g, start, end, weight="weighted_length_m")
            aware_len = sum(
                g[u][v]["base_length_m"] for u, v in zip(aware_path[:-1], aware_path[1:])
            )
            safe_path_found = False
        except nx.NetworkXNoPath:
            aware_path, aware_len = normal_path, normal_len
            safe_path_found = False

    normal_roads, normal_segments = _path_details(g, normal_path)
    aware_roads, aware_segments = _path_details(g, aware_path)

    at_risk = sorted(
        {
            s["road_id"]: s["risk"]
            for s in normal_segments
            if risk_rank.get(s["risk"], 0) >= 3
        }.items(),
        key=lambda x: risk_rank.get(x[1], 0),
        reverse=True,
    )
    blocked = [road_id for road_id, risk in at_risk if risk == "CRITICAL"]

    # An alternate route only counts when it is different from the normal
    # route AND does not contain a critical road.
    alternate_found = (
        normal_path != aware_path
        and safe_path_found
        and not any(seg["risk"] == "CRITICAL" for seg in aware_segments)
    )

    return {
        "profile": profile,
        "normal_route": {
            "path": normal_path,
            "length_m": round(float(normal_len), 1),
            "road_ids": normal_roads,
            "segments": normal_segments,
        },
        "flood_aware_route": {
            "path": aware_path,
            "length_m": round(float(aware_len), 1),
            "road_ids": aware_roads,
            "segments": aware_segments,
        },
        "detour_added_m": round(float(aware_len - normal_len), 1),
        "reroute_required": alternate_found,
        "alternate_route_found": alternate_found,
        "blocked_on_normal_route": bool(blocked),
        "at_risk_roads": [
            {"road_id": road_id, "risk": risk} for road_id, risk in at_risk
        ],
        "blocked_road_ids": blocked,
    }

