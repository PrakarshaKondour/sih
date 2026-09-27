"""
Surface hydrology from a DEM: sink filling, D8 flow direction, flow
accumulation, and low-point (depression) identification.

Real-data note: this module operates on whatever 2D elevation array it is
given. In this repo that array comes from
`ingestion.synthetic_catchment.generate_dem()` (clearly synthetic). Swap in
a real SRTM/Copernicus GeoTIFF read via rasterio (see scripts/fetch_dem.py)
and nothing here changes — that is the whole point of keeping this module
decoupled from ingestion.
"""
from __future__ import annotations
import numpy as np

# D8 neighbor offsets and their "direction codes" (ESRI convention)
D8_OFFSETS = {
    1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1),
    16: (0, -1), 32: (-1, -1), 64: (-1, 0), 128: (-1, 1),
}


def fill_sinks(dem: np.ndarray, max_iter: int = 500) -> np.ndarray:
    """Simple iterative priority-flood-lite sink filling (Planchon-Darboux
    style, simplified): raise each interior cell to the min of its
    neighbors if it is a local pit, repeated until stable or max_iter."""
    filled = dem.copy()
    rows, cols = dem.shape
    for _ in range(max_iter):
        changed = False
        # vectorized neighbor min via padding
        padded = np.pad(filled, 1, mode="edge")
        neighbor_min = np.full_like(filled, np.inf)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                shifted = padded[1 + dr: 1 + dr + rows, 1 + dc: 1 + dc + cols]
                neighbor_min = np.minimum(neighbor_min, shifted)
        pits = (filled < neighbor_min) & (filled == dem)  # avoid raising boundary/plateau forever
        # only fill true interior pits (not the global boundary)
        interior = np.zeros_like(pits)
        interior[1:-1, 1:-1] = True
        to_raise = pits & interior
        if not np.any(to_raise):
            break
        filled = np.where(to_raise, neighbor_min, filled)
        changed = True
        if not changed:
            break
    return filled


def d8_flow_direction(dem: np.ndarray) -> np.ndarray:
    """Return an int array of D8 direction codes (steepest descent)."""
    rows, cols = dem.shape
    padded = np.pad(dem, 1, mode="edge")
    directions = np.zeros((rows, cols), dtype=np.int16)
    best_drop = np.zeros((rows, cols), dtype=np.float64)
    diag = 2 ** 0.5
    for code, (dr, dc) in D8_OFFSETS.items():
        shifted = padded[1 + dr: 1 + dr + rows, 1 + dc: 1 + dc + cols]
        dist = diag if dr != 0 and dc != 0 else 1.0
        drop = (dem - shifted) / dist
        better = drop > best_drop
        directions[better] = code
        best_drop[better] = drop[better]
    return directions


def flow_accumulation(directions: np.ndarray, dem: np.ndarray | None = None) -> np.ndarray:
    """Number of upstream cells draining into each cell.

    Processed in strict topological order (highest elevation first, using
    `dem` if given — falling back to directions-only if not) so that each
    cell's accumulation is finalized before it contributes downstream. This
    avoids the infinite-growth/overflow failure mode a naive repeat-until-
    stable sweep hits whenever D8 direction has even a single 2-cell flat
    cycle (which synthetic/noisy DEMs can produce)."""
    rows, cols = directions.shape
    acc = np.ones((rows, cols), dtype=np.int64)
    targets = {}
    for r in range(rows):
        for c in range(cols):
            code = directions[r, c]
            if code == 0:
                continue
            dr, dc = D8_OFFSETS[code]
            tr, tc = r + dr, c + dc
            if 0 <= tr < rows and 0 <= tc < cols:
                targets[(r, c)] = (tr, tc)

    if dem is None:
        dem = np.zeros((rows, cols))
    order = sorted(((r, c) for r in range(rows) for c in range(cols)),
                     key=lambda rc: -dem[rc[0], rc[1]])

    for (r, c) in order:
        if (r, c) not in targets:
            continue
        tr, tc = targets[(r, c)]
        if (tr, tc) == (r, c):
            continue  # self-loop guard
        acc[tr, tc] += acc[r, c]
    return acc


def identify_low_points(dem: np.ndarray, accumulation: np.ndarray, top_k: int = 8):
    """Return the top_k (row, col) cells with highest flow accumulation —
    i.e. the catchment's natural low points / drainage concentration
    points, used to flag where INFERRED drainage edges should be added
    when real infrastructure doesn't cover a spot (spec section 3)."""
    flat_idx = np.argsort(accumulation.flatten())[::-1][:top_k]
    rows, cols = dem.shape
    return [(int(i // cols), int(i % cols)) for i in flat_idx]


def grid_to_lonlat(row: int, col: int, transform: dict) -> tuple[float, float]:
    res = transform["resolution"]
    lon = transform["min_lon"] + (col / (res - 1)) * (transform["max_lon"] - transform["min_lon"])
    lat = transform["max_lat"] - (row / (res - 1)) * (transform["max_lat"] - transform["min_lat"])
    return lon, lat
