"""
Ties rainfall -> runoff -> drainage graph -> flood depth into a single,
explainable, mass-balance simulation. This is deliberately NOT a full
hydraulic solver (no St. Venant / dynamic wave equations) — per spec
section 4/16 ("do not use ML just for the sake of using ML" and "prefer a
smaller working catchment... over a fake citywide system"), the goal here
is an auditable, physically-motivated approximation good enough to (a)
show a believable 0-3h nowcast and (b) be inverse-calibrated against
historical floods. Every simplifying assumption is flagged inline and
listed in docs/limitations.md.

Method (per timestep, per node):
  1. Runoff generated in each DEM cell (SCS-CN) is assigned to its nearest
     downstream drainage-graph node (a simplified "cell -> nearest node"
     assignment rather than full cell-by-cell flow routing across the
     grid — see docs/limitations.md item 2).
  2. Each node's accumulated inflow is compared against the *sum of
     effective capacities of its outgoing edges* (how much it can pass
     downstream this timestep).
  3. Flow up to capacity is routed to the downstream node(s) (split
     proportionally to capacity if there are multiple outgoing edges).
     Flow beyond capacity is retained as "surcharge volume" and converted
     to a ponding depth using a per-node effective ponding area (nearby
     impervious footprint proxy).
  4. Surcharge volume that is not drained within the timestep carries over
     (simple bucket/reservoir routing), so flooding can persist or drain
     down across the 0-3h horizon.
"""
from __future__ import annotations
import numpy as np
import networkx as nx
from dataclasses import dataclass, field

from app.drainage.capacity import edge_effective_capacity_m3s


@dataclass
class SimulationState:
    node_storage_m3: dict = field(default_factory=dict)   # retained surcharge volume per node
    node_depth_m: dict = field(default_factory=dict)       # derived ponding depth per node
    timestep_log: list = field(default_factory=list)       # list of dicts per timestep, for the UI


DEFAULT_PONDING_AREA_M2 = 400.0  # rough local catchment area draining to one manhole, demo default


def run_simulation(graph: nx.DiGraph, node_inflow_m3_per_timestep: list[dict],
                     effective_factor_override: dict | None = None,
                     timestep_s: int = 3600,
                     ponding_area_m2: float | dict = DEFAULT_PONDING_AREA_M2
                     ) -> SimulationState:
    """
    node_inflow_m3_per_timestep: list (length = n_timesteps) of
        {node_id: inflow_volume_m3_this_timestep}
    Returns a SimulationState with per-node depth logged at every timestep.
    """
    state = SimulationState()
    for node in graph.nodes:
        state.node_storage_m3[node] = 0.0

    # precompute effective capacity per edge (m3/s) -> m3 per timestep
    edge_capacity_m3 = {}
    for u, v, attrs in graph.edges(data=True):
        cap_m3s = edge_effective_capacity_m3s(attrs, effective_factor_override)
        edge_capacity_m3[(u, v)] = cap_m3s * timestep_s

    for t, inflow_map in enumerate(node_inflow_m3_per_timestep):
        # 1. add this timestep's runoff inflow to each node's storage
        for node, vol in inflow_map.items():
            if node in state.node_storage_m3:
                state.node_storage_m3[node] += vol

        # 2. route what capacity allows, downstream, for every node (process
        #    in a stable order; small graph, a couple of passes is enough
        #    for the demo catchment to converge within a timestep)
        for _pass in range(3):
            deltas = {n: 0.0 for n in graph.nodes}
            for node in graph.nodes:
                available = state.node_storage_m3[node]
                if available <= 0:
                    continue
                out_edges = list(graph.out_edges(node, data=True))
                total_cap = sum(edge_capacity_m3.get((u, v), 0.0) for u, v, _ in out_edges)
                if total_cap <= 0 or not out_edges:
                    continue
                to_route = min(available, total_cap)
                for u, v, _attrs in out_edges:
                    cap = edge_capacity_m3.get((u, v), 0.0)
                    share = (cap / total_cap) * to_route if total_cap > 0 else 0
                    deltas[u] -= share
                    deltas[v] += share
            for n in graph.nodes:
                state.node_storage_m3[n] = max(0.0, state.node_storage_m3[n] + deltas[n])

        # 3. convert any node with positive storage beyond what its own
        #    outgoing capacity could carry away this step into a ponding
        #    depth (only nodes with little/no outgoing capacity accumulate
        #    persistent storage -- e.g. sinks / low points / inferred nodes)
        depths_this_step = {}
        for node in graph.nodes:
            vol = state.node_storage_m3[node]
            area = ponding_area_m2.get(node, DEFAULT_PONDING_AREA_M2) if isinstance(ponding_area_m2, dict) else ponding_area_m2
            depth = vol / max(area, 1.0)  # simple mass-balance: depth = volume / ponding area
            depths_this_step[node] = round(float(depth), 4)
        state.node_depth_m = depths_this_step
        state.timestep_log.append({"t_index": t, "depths_m": dict(depths_this_step)})

    return state


def assign_runoff_to_nearest_node(runoff_grid_mm: np.ndarray, transform: dict,
                                     graph: nx.DiGraph, cell_area_m2: float) -> dict:
    """Nearest-neighbor assignment of each DEM cell's runoff volume to the
    nearest drainage-graph node (documented simplification — see module
    docstring). Returns {node_id: volume_m3}."""
    from app.hydrology.dem_processing import grid_to_lonlat

    node_ids = []
    node_lonlat = []
    for node_id, attrs in graph.nodes(data=True):
        geom = attrs.get("geometry")
        if geom is None:
            continue
        lon, lat = (geom.x, geom.y) if hasattr(geom, "x") else geom
        node_ids.append(node_id)
        node_lonlat.append((lon, lat))
    node_lonlat = np.array(node_lonlat)

    rows, cols = runoff_grid_mm.shape
    inflow = {nid: 0.0 for nid in node_ids}
    for r in range(rows):
        for c in range(cols):
            mm = runoff_grid_mm[r, c]
            if mm <= 0:
                continue
            lon, lat = grid_to_lonlat(r, c, transform)
            d2 = (node_lonlat[:, 0] - lon) ** 2 + (node_lonlat[:, 1] - lat) ** 2
            nearest_idx = int(np.argmin(d2))
            vol_m3 = mm / 1000.0 * cell_area_m2
            inflow[node_ids[nearest_idx]] += vol_m3
    return inflow


PONDING_AREA_FRACTION = 0.08
"""Documented assumption: of the total catchment area that drains to a
given node, only this fraction is assumed to actually pond at/near that
node (the rest is assumed to spread across adjacent street/open ground
rather than stack up to full catchment-area-equivalent depth at a single
point). This is a simplification flagged in docs/limitations.md, not a
calibrated or measured value."""


def compute_node_ponding_areas(graph: nx.DiGraph, transform: dict, cell_area_m2: float,
                                  ponding_area_fraction: float = PONDING_AREA_FRACTION,
                                  min_area_m2: float = DEFAULT_PONDING_AREA_M2) -> dict:
    """Static (rainfall-independent) per-node ponding-area estimate, based
    on how many DEM cells are nearest-assigned to that node (i.e. its
    contributing catchment area), scaled by PONDING_AREA_FRACTION."""
    import numpy as np
    from app.hydrology.dem_processing import grid_to_lonlat

    node_ids, node_lonlat = [], []
    for node_id, attrs in graph.nodes(data=True):
        geom = attrs.get("geometry")
        if geom is None:
            continue
        lon, lat = (geom.x, geom.y) if hasattr(geom, "x") else geom
        node_ids.append(node_id)
        node_lonlat.append((lon, lat))
    node_lonlat = np.array(node_lonlat)

    res = transform["resolution"]
    counts = {nid: 0 for nid in node_ids}
    for r in range(res):
        for c in range(res):
            lon, lat = grid_to_lonlat(r, c, transform)
            d2 = (node_lonlat[:, 0] - lon) ** 2 + (node_lonlat[:, 1] - lat) ** 2
            counts[node_ids[int(np.argmin(d2))]] += 1

    return {nid: max(min_area_m2, counts[nid] * cell_area_m2 * ponding_area_fraction)
            for nid in node_ids}


def depth_to_risk(depth_m: float, thresholds) -> str:
    if depth_m >= thresholds.critical_m:
        return "CRITICAL"
    if depth_m >= thresholds.high_m:
        return "HIGH"
    if depth_m >= thresholds.moderate_m:
        return "MODERATE"
    if depth_m >= thresholds.low_m:
        return "LOW"
    return "NONE"
