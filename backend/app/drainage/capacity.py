"""
Manning's-equation capacity model for drainage edges, and the
physical-vs-effective capacity separation required by spec section 5.

    Q = (1/n) * A * R^(2/3) * S^(1/2)

n     = Manning's roughness coefficient (material-dependent)
A     = flow cross-sectional area (m^2)
R     = hydraulic radius (m) = A / wetted perimeter
S     = bed/pipe slope (m/m)

Physical capacity: computed directly from geometry + Manning's n, assuming
the conduit is clean and undamaged. This is what you'd get from as-built
drawings.

Effective capacity: physical capacity scaled by an "effective_factor" in
(0, 1] that represents blockage/sedimentation/poor condition/undocumented
connections/inlet losses. This factor starts at a material/edge-type prior
and is exactly what `calibration/inverse_calibration.py` tunes against
observed floods — i.e. this file defines the parameter space that
inverse-calibration searches, it does not itself estimate the factor from
data.
"""
from __future__ import annotations
import numpy as np

MANNING_N = {
    "RCC": 0.013,
    "stoneware": 0.015,
    "PVC": 0.011,
    "open_nala_earth": 0.030,
    "open_nala_lined": 0.020,
}

# Prior (pre-calibration) effective-capacity factors by edge type/material —
# these are ENGINEERING ASSUMPTIONS, not measurements. They exist to give
# inverse calibration a defensible, documented starting point rather than
# starting from an arbitrary 1.0. See docs/limitations.md.
DEFAULT_EFFECTIVE_FACTOR = {
    "piped_sewer": 0.70,       # assume some sedimentation/undocumented connections
    "open_channel": 0.55,      # nalas in dense urban Hyderabad commonly encroached/silted
    "outfall_connector": 0.60,
    "inferred_flowpath": 0.40,  # unverified, treat conservatively
}


def circular_pipe_capacity_m3s(diameter_mm: float, slope: float, material: str = "RCC",
                                  fill_ratio: float = 1.0) -> float:
    """Manning's Q for a circular pipe. fill_ratio<1 models partial flow
    (simplified as area/perimeter scaled by fill_ratio^ (2/3) approx — a
    common engineering shortcut for a fast, explainable estimate; a full
    partial-flow geometry solve is documented as a limitation)."""
    if diameter_mm is None or (isinstance(diameter_mm, float) and np.isnan(diameter_mm)):
        return 0.0
    d = diameter_mm / 1000.0
    n = MANNING_N.get(material, 0.013)
    a_full = np.pi * (d / 2) ** 2
    p_full = np.pi * d
    r_full = a_full / p_full
    q_full = (1 / n) * a_full * r_full ** (2 / 3) * max(slope, 0.0003) ** 0.5
    return float(q_full * fill_ratio)


def trapezoidal_nala_capacity_m3s(width_m: float, depth_m: float, slope: float,
                                     lined: bool = False, side_slope: float = 1.0) -> float:
    """Manning's Q for a trapezoidal open channel (typical nala cross
    section). side_slope=1.0 means 1H:1V banks (typical earthen nala)."""
    n = MANNING_N["open_nala_lined" if lined else "open_nala_earth"]
    b, y = width_m, depth_m
    a = (b + side_slope * y) * y
    p = b + 2 * y * (1 + side_slope ** 2) ** 0.5
    r = a / p if p > 0 else 0
    q = (1 / n) * a * r ** (2 / 3) * max(slope, 0.0005) ** 0.5
    return float(q)


def edge_physical_capacity_m3s(edge_attrs: dict) -> float:
    kind = edge_attrs.get("edge_kind")
    if kind == "piped_sewer" or kind == "outfall_connector":
        return circular_pipe_capacity_m3s(
            edge_attrs.get("diameter_mm", 300), edge_attrs.get("slope", 0.002),
            edge_attrs.get("material", "RCC"))
    if kind == "open_channel":
        return trapezoidal_nala_capacity_m3s(
            edge_attrs.get("existing_width_m", 3.0), edge_attrs.get("depth_m", 1.5),
            edge_attrs.get("slope", 0.001))
    return 0.0  # inferred_flowpath: no physical capacity, purely a flow path


def edge_effective_capacity_m3s(edge_attrs: dict, effective_factor_override: dict | None = None) -> float:
    """Physical capacity * effective_factor. `effective_factor_override`
    is keyed by edge_id and is what calibration writes back after
    optimization; falls back to the DEFAULT_EFFECTIVE_FACTOR prior."""
    physical = edge_physical_capacity_m3s(edge_attrs)
    edge_id = edge_attrs.get("edge_id")
    if effective_factor_override and edge_id in effective_factor_override:
        factor = effective_factor_override[edge_id]
    else:
        factor = DEFAULT_EFFECTIVE_FACTOR.get(edge_attrs.get("edge_kind"), 0.5)
    return physical * factor
