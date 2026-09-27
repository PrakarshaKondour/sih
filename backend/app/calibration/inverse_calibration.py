"""
Inverse calibration (spec section 6): given a historical rainfall event and
observed flood extent, optimize the uncertain effective-capacity
parameters so the model's predicted flooded nodes best match what was
observed.

Parameter space kept deliberately small and physically meaningful: one
effective-capacity multiplier per edge_kind (piped_sewer, open_channel,
outfall_connector, inferred_flowpath) rather than one per edge — with a
handful of historical events and a demo-sized network, per-edge
calibration would simply overfit. This also keeps the optimizer fast
enough for scipy.optimize.minimize to be practical (section 6 explicitly
says "use scipy.optimize first").

Metrics reported: precision, recall, F1 on flooded/not-flooded node
classification at a configurable depth threshold, plus mean depth error at
nodes with observed depth (we do not have per-node observed depth in the
synthetic ground truth, so depth-error reporting is left as NaN/None and
explicitly labeled unavailable rather than faked).
"""
from __future__ import annotations
import numpy as np
from scipy.optimize import minimize
import networkx as nx
import geopandas as gpd
from shapely.geometry import Point

from app.hydrology.simulation import run_simulation

EDGE_KINDS = ["piped_sewer", "open_channel", "outfall_connector", "inferred_flowpath"]


def nodes_within_observed_flood(graph: nx.DiGraph, observed_flood_polys: gpd.GeoDataFrame) -> set:
    """Which graph nodes fall inside any observed-flood polygon -> the
    ground-truth positive set for classification metrics."""
    positive = set()
    for node_id, attrs in graph.nodes(data=True):
        geom = attrs.get("geometry")
        if geom is None:
            continue
        pt = geom if hasattr(geom, "x") else Point(geom)
        for _, poly_row in observed_flood_polys.iterrows():
            if poly_row.geometry.contains(pt) or poly_row.geometry.distance(pt) < 0.0008:
                positive.add(node_id)
                break
    return positive


def predicted_flooded_nodes(state, depth_threshold_m: float = 0.10) -> set:
    return {n for n, d in state.node_depth_m.items() if d >= depth_threshold_m}


def classification_metrics(predicted: set, observed: set, all_nodes: set) -> dict:
    tp = len(predicted & observed)
    fp = len(predicted - observed)
    fn = len(observed - predicted)
    tn = len(all_nodes - predicted - observed)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _factors_from_vector(x: np.ndarray) -> dict:
    # x is 4 values in (0,1], mapped via sigmoid-ish clip to stay in a
    # physically sane range (0.05 - 1.0)
    clipped = np.clip(x, 0.05, 1.0)
    return {kind: float(clipped[i]) for i, kind in enumerate(EDGE_KINDS)}


def _run_and_score(x: np.ndarray, graph: nx.DiGraph, node_inflow_series: list[dict],
                     observed_positive: set, depth_threshold_m: float,
                     ponding_area_m2=None) -> tuple[float, dict]:
    factor_by_kind = _factors_from_vector(x)
    # expand kind->factor into a per-edge override dict keyed by edge_id,
    # since capacity.edge_effective_capacity_m3s expects per-edge overrides
    overrides = {}
    for u, v, attrs in graph.edges(data=True):
        kind = attrs.get("edge_kind")
        eid = attrs.get("edge_id", f"{u}->{v}")
        overrides[eid] = factor_by_kind.get(kind, 0.5)
        attrs["edge_id"] = eid  # ensure edge_id present for lookup in capacity fn

    kwargs = {}
    if ponding_area_m2 is not None:
        kwargs["ponding_area_m2"] = ponding_area_m2
    state = run_simulation(graph, node_inflow_series, effective_factor_override=overrides, **kwargs)
    predicted = predicted_flooded_nodes(state, depth_threshold_m)
    metrics = classification_metrics(predicted, observed_positive, set(graph.nodes))

    # Classification F1 is a step function of the capacity factors (a small
    # factor change only matters when it flips a node across the depth
    # threshold), which gives scipy.optimize.minimize very little gradient
    # signal to climb on a small demo network. So the actual OPTIMIZATION
    # objective is a continuous hinge loss on depth vs. threshold (still
    # physically meaningful: push observed-flooded nodes' predicted depth
    # up to the threshold, push observed-dry nodes' predicted depth down
    # to it) -- F1/precision/recall above are reported as the interpretable
    # metrics, but are not themselves what the optimizer directly follows.
    hinge = 0.0
    for node, depth in state.node_depth_m.items():
        if node in observed_positive:
            hinge += max(0.0, depth_threshold_m - depth)  # under-predicted a real flood
        else:
            hinge += max(0.0, depth - depth_threshold_m)  # over-predicted a dry node
    loss = hinge / max(len(state.node_depth_m), 1)
    return loss, metrics


def calibrate(graph: nx.DiGraph, node_inflow_series: list[dict],
               observed_flood_polys: gpd.GeoDataFrame, depth_threshold_m: float = 0.10,
               max_iter: int = 25, ponding_area_m2=None) -> dict:
    """Runs BEFORE (default priors) then optimizes, returns before/after
    metrics and the calibrated factors. Uses Nelder-Mead (scipy.optimize)
    since the loss surface (through the routing loop) isn't guaranteed
    smooth/differentiable."""
    observed_positive = nodes_within_observed_flood(graph, observed_flood_polys)

    x0 = np.array([0.70, 0.55, 0.60, 0.40])  # same as capacity.DEFAULT_EFFECTIVE_FACTOR
    loss_before, metrics_before = _run_and_score(x0, graph, node_inflow_series,
                                                    observed_positive, depth_threshold_m, ponding_area_m2)

    result = minimize(
        lambda x: _run_and_score(x, graph, node_inflow_series, observed_positive, depth_threshold_m, ponding_area_m2)[0],
        x0, method="Nelder-Mead",
        options={"maxiter": max_iter, "xatol": 0.02, "fatol": 0.01},
    )

    loss_after, metrics_after = _run_and_score(result.x, graph, node_inflow_series,
                                                  observed_positive, depth_threshold_m, ponding_area_m2)
    calibrated_factors = _factors_from_vector(result.x)

    return {
        "before_calibration": {"factors": _factors_from_vector(x0), "metrics": metrics_before},
        "after_calibration": {"factors": calibrated_factors, "metrics": metrics_after},
        "n_observed_flooded_nodes": len(observed_positive),
        "n_total_nodes": graph.number_of_nodes(),
        "optimizer_converged": bool(result.success),
        "optimizer_iterations": int(result.nit),
        "depth_error_m": None,  # no per-node observed depth available -- see module docstring
        "note": "SYNTHETIC VALIDATION: observed-flood ground truth is synthetic "
                "(see docs/historical_event_13oct2020.md); metrics show the "
                "calibration MECHANISM working (factors move away from priors "
                "to minimize a continuous depth-vs-threshold hinge loss), not "
                "real-world skill. For this specific record-intensity event "
                "(192mm/6h) F1 may not move much even though the underlying "
                "factors do: at that rainfall intensity most of the demo "
                "catchment exceeds capacity regardless of the tested effective- "
                "capacity range, which is itself consistent with how "
                "catastrophic the real 13 Oct 2020 event was city-wide. The "
                "calibration mechanism is more clearly visible at moderate "
                "rainfall intensities (see docs/limitations.md).",
    }
