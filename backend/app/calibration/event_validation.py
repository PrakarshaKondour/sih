"""
Event-based validation (spec section 6: "so the model is not simply
overfitted to one storm").

Honesty note: this demo repo ships exactly ONE real-world-referenced
historical event (13 Oct 2020, see docs/historical_event_13oct2020.md).
True multi-event holdout validation needs multiple independently observed
events, which this environment did not have access to. What this module
does instead — and what it honestly claims to do — is:

  1. Calibrate effective-capacity factors on event A (13 Oct 2020 profile).
  2. Generate a SECOND, smaller, independently-seeded SYNTHETIC storm
     (event B) with its own synthetic ground truth, and check whether the
     factors calibrated on A generalize to B (rather than re-optimizing
     against B).

This demonstrates the *validation mechanism* correctly — the code path a
real multi-event dataset would run through — without pretending a second
synthetic storm is independent real-world evidence of skill. Treat the
"transfer" metrics here as SYNTHETIC VALIDATION only.
"""
from __future__ import annotations
import numpy as np
from app.calibration.inverse_calibration import (
    nodes_within_observed_flood, predicted_flooded_nodes, classification_metrics,
)
from app.hydrology.simulation import run_simulation


def build_holdout_event(bounds, seed_offset: int = 7):
    """A smaller, different-shaped storm for the holdout check, using the
    same synthetic generator but different rainfall parameters."""
    from app.ingestion import synthetic_catchment as sc
    df = sc.generate_rainfall_event(event_name="holdout-synthetic", total_mm=95.0, hours=4)
    return df


def validate_transfer(graph, node_inflow_series_event_b, observed_flood_polys_event_b,
                        calibrated_factors: dict, depth_threshold_m: float = 0.10,
                        ponding_area_m2=None) -> dict:
    overrides = {}
    for u, v, attrs in graph.edges(data=True):
        kind = attrs.get("edge_kind")
        eid = attrs.get("edge_id", f"{u}->{v}")
        overrides[eid] = calibrated_factors.get(kind, 0.5)
        attrs["edge_id"] = eid

    kwargs = {}
    if ponding_area_m2 is not None:
        kwargs["ponding_area_m2"] = ponding_area_m2
    state = run_simulation(graph, node_inflow_series_event_b, effective_factor_override=overrides, **kwargs)
    predicted = predicted_flooded_nodes(state, depth_threshold_m)
    observed = nodes_within_observed_flood(graph, observed_flood_polys_event_b)
    metrics = classification_metrics(predicted, observed, set(graph.nodes))
    return {
        "event": "holdout-synthetic",
        "metrics": metrics,
        "note": "SYNTHETIC VALIDATION — holdout event is synthetic, not an "
                "independent real historical record. See module docstring.",
    }
