"""
Builds the drainage digital twin as a NetworkX directed graph:
  nodes = manholes / nala junctions / outfalls
  edges = piped sewer segments / open nala segments / inferred connectors

This is graph structure ONLY — it does not decide capacity (see
drainage/capacity.py) or run flow simulation (see hydrology + calibration).
Keeping it separate means the same graph works whether it was built from
real TGRAC layers or the synthetic generator, per the source/confidence
tagging in core/schemas.py.
"""
from __future__ import annotations
import networkx as nx
import geopandas as gpd
import numpy as np


def build_drainage_graph(manholes: gpd.GeoDataFrame, sewerlines: gpd.GeoDataFrame,
                           nala: gpd.GeoDataFrame, connectors: gpd.GeoDataFrame
                           ) -> nx.DiGraph:
    g = nx.DiGraph()

    for _, row in manholes.iterrows():
        g.add_node(row["node_id"], kind="manhole", geometry=row.geometry,
                    elevation_m=row["elevation_m"], invert_level_m=row["invert_level_m"],
                    source=row["source"], confidence=row["confidence"])

    # nala junction nodes are implicit endpoints of nala edges
    for _, row in nala.iterrows():
        for node_id, pt in ((row["from_node"], row.geometry.coords[0]),
                              (row["to_node"], row.geometry.coords[-1])):
            if node_id not in g:
                g.add_node(node_id, kind="nala_junction", geometry=pt,
                            source=row["source"], confidence=row["confidence"])

    for edge_df, edge_kind in ((sewerlines, "piped_sewer"), (nala, "open_channel"),
                                 (connectors, "outfall_connector")):
        if edge_df is None or len(edge_df) == 0:
            continue
        for _, row in edge_df.iterrows():
            u, v = row["from_node"], row["to_node"]
            if u not in g:
                g.add_node(u, kind="implicit", source=row["source"], confidence=row["confidence"])
            if v not in g:
                g.add_node(v, kind="implicit", source=row["source"], confidence=row["confidence"])
            attrs = dict(row.drop(labels=[c for c in ("from_node", "to_node") if c in row.index]))
            attrs["edge_kind"] = edge_kind
            attrs["length_m"] = _geodesic_length_m(row.geometry)
            g.add_edge(u, v, **attrs)

    return g


def _geodesic_length_m(geom) -> float:
    """Rough length in meters from a lon/lat LineString (equirectangular
    approx — fine at this catchment's scale, ~2km)."""
    coords = list(geom.coords)
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coords[:-1], coords[1:]):
        dx = (lon2 - lon1) * 111_320 * np.cos(np.radians((lat1 + lat2) / 2))
        dy = (lat2 - lat1) * 110_540
        total += (dx ** 2 + dy ** 2) ** 0.5
    return total


def add_inferred_edges_from_low_points(g: nx.DiGraph, low_points_lonlat: list[tuple[float, float]],
                                         max_link_deg: float = 0.004):
    """Where flow-accumulation low points (from DEM analysis) are not near
    any real/synthetic-real drainage node, add an INFERRED edge connecting
    them to the nearest existing node, tagged low confidence, per spec
    section 3 ("mark these edges/nodes as INFERRED, assign lower
    confidence")."""
    from shapely.geometry import Point
    added = []
    for i, (lon, lat) in enumerate(low_points_lonlat):
        pt = Point(lon, lat)
        nearest = None
        nearest_d = float("inf")
        for node_id, data in g.nodes(data=True):
            geom = data.get("geometry")
            if geom is None:
                continue
            gx, gy = (geom.x, geom.y) if hasattr(geom, "x") else geom
            d = ((gx - lon) ** 2 + (gy - lat) ** 2) ** 0.5
            if d < nearest_d:
                nearest_d, nearest = d, node_id
        if nearest is None:
            continue
        new_node = f"INFERRED-LP-{i:03d}"
        g.add_node(new_node, kind="inferred_low_point", geometry=(lon, lat),
                    source="inferred:dem", confidence=0.35)
        if nearest_d <= max_link_deg:
            g.add_edge(new_node, nearest, edge_kind="inferred_flowpath",
                        source="inferred:dem", confidence=0.3,
                        length_m=nearest_d * 111_000, diameter_mm=np.nan)
            added.append((new_node, nearest))
    return added
