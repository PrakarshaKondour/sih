"""
Explainable rainfall -> runoff baseline using the SCS Curve Number (CN)
method. Deliberately NOT a black-box ML model, per spec section 4 ("Do not
use ML just for the sake of using ML"): CN is the standard, auditable
method used in real drainage engineering practice, and its single free
parameter (CN) has a direct physical interpretation (higher CN = more
impervious surface = more runoff), which is exactly the kind of parameter
the inverse-calibration step (section 6) tunes.

Reference: SCS-CN method, Q = (P - Ia)^2 / (P - Ia + S), S = 25400/CN - 254 (mm)
Ia (initial abstraction) = 0.2 * S (standard assumption, configurable).
"""
from __future__ import annotations
import numpy as np


def imperviousness_to_cn(imperviousness: np.ndarray, cn_pervious: float = 61.0,
                           cn_impervious: float = 98.0) -> np.ndarray:
    """Blend a per-cell curve number from imperviousness fraction [0,1].
    cn_pervious=61 (open space, fair condition) and cn_impervious=98
    (paved) are standard SCS TR-55 reference values."""
    return cn_pervious + imperviousness * (cn_impervious - cn_pervious)


def scs_cn_runoff(rainfall_mm: float, cn: np.ndarray, ia_ratio: float = 0.2) -> np.ndarray:
    """Return runoff depth (mm) per cell for a given rainfall depth (mm).
    Vectorized over the CN raster."""
    s = (25400.0 / np.clip(cn, 30, 99)) - 254.0
    ia = ia_ratio * s
    p = rainfall_mm
    numerator = np.clip(p - ia, 0, None) ** 2
    denominator = (p - ia + s)
    denominator = np.where(denominator <= 0, np.nan, denominator)
    q = numerator / denominator
    return np.nan_to_num(q, nan=0.0)


def runoff_timeseries(rainfall_hyetograph_mm: np.ndarray, cn: np.ndarray) -> np.ndarray:
    """Apply SCS-CN cumulatively across an hourly hyetograph (accounts for
    the fact that CN runoff is a function of *cumulative* storm rainfall,
    not independent per-hour rainfall). Returns incremental runoff per
    timestep, shape (T, *cn.shape)."""
    cum_rain = np.cumsum(rainfall_hyetograph_mm)
    cum_runoff = np.array([scs_cn_runoff(p, cn) for p in cum_rain])
    incremental = np.diff(cum_runoff, axis=0, prepend=np.zeros((1, *cn.shape)))
    return np.clip(incremental, 0, None)


def runoff_volume_m3(runoff_mm: np.ndarray, cell_area_m2: float) -> np.ndarray:
    """mm depth -> m^3 volume per cell."""
    return runoff_mm / 1000.0 * cell_area_m2
