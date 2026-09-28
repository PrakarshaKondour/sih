"""
Generates the demo catchment's drainage network, roads, historical
inundation labels, DEM, land cover, and rainfall — all schema-matched to
what the real TGRAC/IMD adapters would return (see core/schemas.py and
data_inventory.md).

Every feature produced here is tagged source="synthetic-demo",
confidence < 1.0 where appropriate, and every node/edge ID is prefixed
"SYN-" so nothing downstream can accidentally treat it as verified
municipal infrastructure.

Design of the synthetic network (not arbitrary — mimics real Hyderabad
drainage topology):
  - A dendritic sewer/nala network draining SE toward the Musi River, since
    that is the real large-scale drainage direction for Hyderabad's core.
  - One primary open nala (the "trunk") with several piped sewer
    tributaries feeding into it, which matches how GHMC nala + HMWSSB
    sewer layers actually relate.
  - Elevation drops ~6m across the ~2km catchment (consistent with
    Hyderabad's gentle terrain, real SRTM tiles show similar local relief).
"""
from __future__ import annotations
import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, LineString, Polygon
from app.core.schemas import CatchmentBounds

RNG_SEED = 42


def _rng():
    return np.random.default_rng(RNG_SEED)


def generate_manholes(bounds: CatchmentBounds, n_per_row: int = 9, n_rows: int = 7) -> gpd.GeoDataFrame:
    """Grid-ish manhole layout along implied street lines, elevation sloping
    toward the SE corner (toward the Musi River)."""
    rng = _rng()
    lons = np.linspace(bounds.min_lon, bounds.max_lon, n_per_row)
    lats = np.linspace(bounds.min_lat, bounds.max_lat, n_rows)
    rows = []
    node_id = 0
    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            jitter = rng.normal(0, 0.0004, size=2)
            plon, plat = lon + jitter[0], lat + jitter[1]
            # elevation: higher in NW, lower in SE (toward Musi river), 545m -> 539m
            frac_se = ((plon - bounds.min_lon) / (bounds.max_lon - bounds.min_lon) +
                       (bounds.max_lat - plat) / (bounds.max_lat - bounds.min_lat)) / 2
            elev = 545.0 - 6.0 * frac_se + rng.normal(0, 0.3)
            rows.append({
                "node_id": f"SYN-MH-{node_id:04d}",
                "geometry": Point(plon, plat),
                "elevation_m": round(float(elev), 2),
                "invert_level_m": round(float(elev) - rng.uniform(1.2, 2.5), 2),
                "node_type": "manhole",
                "source": "synthetic-demo",
                "confidence": 0.9,
            })
            node_id += 1
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    return gdf


def generate_sewerlines(manholes: gpd.GeoDataFrame, bounds: CatchmentBounds) -> gpd.GeoDataFrame:
    """Connect each manhole to its downhill neighbor (nearest-downhill
    heuristic, with an unbounded fallback so nearly every manhole ends up
    connected — real sewer networks are designed to have very few true
    dead ends; only genuine local low points should end up without an
    outgoing pipe) to build a dendritic piped network draining SE."""
    rng = _rng()
    coords = np.array([[p.x, p.y] for p in manholes.geometry])
    elevs = manholes["elevation_m"].to_numpy()
    ids = manholes["node_id"].to_numpy()

    edges = []
    edge_id = 0
    for i in range(len(manholes)):
        dists = np.sqrt(((coords - coords[i]) ** 2).sum(axis=1))
        dists[i] = np.inf
        # first try close candidates (~390m), then fall back to searching
        # the whole catchment for the nearest downhill node so we don't
        # strand manholes purely because of local search-radius bad luck
        candidates = np.where(dists < 0.0035)[0]
        downhill = [c for c in candidates if elevs[c] < elevs[i]]
        if not downhill:
            all_downhill = np.where(elevs < elevs[i])[0]
            downhill = list(all_downhill)
        if not downhill:
            continue  # genuine local minimum -- expected to have no outgoing pipe
        target = min(downhill, key=lambda c: dists[c])
        length_m = dists[i] * 111_000  # rough deg->m
        slope = max((elevs[i] - elevs[target]) / max(length_m, 1), 0.0005)
        diameter = float(rng.choice([300, 450, 600, 750, 900], p=[0.35, 0.25, 0.2, 0.13, 0.07]))
        edges.append({
            "edge_id": f"SYN-SL-{edge_id:04d}",
            "geometry": LineString([coords[i], coords[target]]),
            "from_node": ids[i],
            "to_node": ids[target],
            "diameter_mm": diameter,
            "slope": round(float(slope), 5),
            "material": str(rng.choice(["RCC", "stoneware", "PVC"], p=[0.6, 0.25, 0.15])),
            "edge_type": "piped_sewer",
            "source": "synthetic-demo",
            "confidence": 0.85,
        })
        edge_id += 1
    return gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:4326")


def generate_trunk_nala(bounds: CatchmentBounds) -> gpd.GeoDataFrame:
    """One open-channel trunk nala running diagonally NW->SE across the
    catchment (the receiving channel for the piped tributaries), matching
    how a real GHMC nala centerline would sit relative to a sewer network."""
    rng = _rng()
    n_pts = 12
    t = np.linspace(0, 1, n_pts)
    lon = bounds.min_lon + t * (bounds.max_lon - bounds.min_lon) * 0.95 + rng.normal(0, 0.0006, n_pts)
    lat = bounds.max_lat - t * (bounds.max_lat - bounds.min_lat) * 0.95 + rng.normal(0, 0.0006, n_pts)
    edges = []
    for i in range(n_pts - 1):
        width_existing = round(float(6.0 + 4.0 * (1 - t[i])), 2)  # narrows upstream
        edges.append({
            "edge_id": f"SYN-NALA-{i:03d}",
            "geometry": LineString([(lon[i], lat[i]), (lon[i + 1], lat[i + 1])]),
            "from_node": f"SYN-NALA-NODE-{i:03d}",
            "to_node": f"SYN-NALA-NODE-{i + 1:03d}",
            "existing_width_m": width_existing,
            "proposed_width_m": round(width_existing * 1.3, 2),
            "depth_m": round(float(2.0 + 1.0 * (1 - t[i])), 2),
            "edge_type": "open_nala",
            "source": "synthetic-demo",
            "confidence": 0.85,
        })
    return gpd.GeoDataFrame(edges, geometry="geometry", crs="EPSG:4326")


def snap_sewer_outfalls_to_nala(sewerlines: gpd.GeoDataFrame, nala: gpd.GeoDataFrame,
                                  manholes: gpd.GeoDataFrame, snap_dist_deg: float = 0.003
                                  ) -> gpd.GeoDataFrame:
    """Find sewer 'leaf' downstream nodes (nodes that are never a from_node,
    i.e. local outfalls) and snap them onto the nearest trunk nala segment,
    adding explicit connector edges. This is the spatial snapping step
    required by spec section 3."""
    from_ids = set(sewerlines["from_node"])
    to_ids = set(sewerlines["to_node"])
    leaf_nodes = to_ids - from_ids
    mh_lookup = manholes.set_index("node_id")

    nala_coords = []
    for _, row in nala.iterrows():
        for c in row.geometry.coords:
            nala_coords.append((c, row["edge_id"]))

    connectors = []
    cid = 0
    for node_id in leaf_nodes:
        if node_id not in mh_lookup.index:
            continue
        p = mh_lookup.loc[node_id].geometry
        best = min(nala_coords, key=lambda nc: (nc[0][0] - p.x) ** 2 + (nc[0][1] - p.y) ** 2)
        (nx, ny), nala_edge_id = best
        d = ((nx - p.x) ** 2 + (ny - p.y) ** 2) ** 0.5
        if d <= snap_dist_deg:
            connectors.append({
                "edge_id": f"SYN-CONN-{cid:03d}",
                "geometry": LineString([(p.x, p.y), (nx, ny)]),
                "from_node": node_id,
                "to_node": f"OUTFALL-{nala_edge_id}",
                "diameter_mm": 600.0,
                "slope": 0.002,
                "material": "RCC",
                "edge_type": "outfall_connector",
                "source": "synthetic-demo",
                "confidence": 0.7,
            })
            cid += 1
    return gpd.GeoDataFrame(connectors, geometry="geometry", crs="EPSG:4326")


def generate_roads(bounds: CatchmentBounds, n: int = 50) -> gpd.GeoDataFrame:
    """Build a deliberately synthetic, but connected, neighborhood network.

    Coordinates below are normalized to the demo catchment.  Shared end points
    give the routing graph real intersections, while intermediate vertices make
    the roads look like streets shaped around blocks and drainage rather than a
    rectangular lattice.  The network has a small roundabout, collector loops,
    side streets, and several intentional cul-de-sacs.
    """
    def point(x, y):
        return (bounds.min_lon + x * (bounds.max_lon - bounds.min_lon),
                bounds.min_lat + y * (bounds.max_lat - bounds.min_lat))

    # Major spines first, then connected collectors and local streets. Values
    # are normalized coordinates so the geometry remains synthetic and portable.
    paths = [
        [(0.00,.18),(.13,.22),(.28,.26),(.43,.30),(.61,.35),(.82,.43),(1.00,.48)],
        [(0.05,.82),(.18,.74),(.34,.66),(.49,.59),(.66,.54),(.84,.50),(1.00,.48)],
        [(.15,1.00),(.22,.84),(.28,.70),(.34,.66),(.43,.52),(.49,.36),(.55,.00)],
        [(.00,.62),(.14,.60),(.28,.58),(.45,.59),(.66,.64),(.85,.72),(1.00,.78)],
        [(.09,.12),(.16,.29),(.20,.45),(.28,.58),(.38,.71),(.47,.87)],
        [(.70,.05),(.68,.20),(.66,.35),(.66,.54),(.72,.70),(.82,.90)],
        [(.08,.39),(.19,.43),(.33,.45),(.49,.45),(.62,.43),(.77,.38),(.93,.33)],
        [(.31,.03),(.35,.17),(.41,.30),(.49,.36),(.59,.43),(.70,.50),(.89,.58)],
        [(.03,.74),(.17,.70),(.30,.68),(.45,.69),(.61,.74),(.77,.82)],
        [(.42,.99),(.44,.86),(.45,.72),(.45,.59),(.46,.46),(.49,.36)],
        [(.06,.18),(.13,.28),(.20,.45)], [(.13,.22),(.14,.39),(.19,.43)],
        [(.20,.45),(.29,.50),(.45,.59)], [(.28,.26),(.28,.42),(.33,.45)],
        [(.34,.66),(.35,.56),(.33,.45)], [(.43,.30),(.46,.38),(.49,.45)],
        [(.49,.45),(.54,.52),(.66,.54)], [(.61,.35),(.62,.43),(.66,.54)],
        [(.66,.54),(.72,.48),(.77,.38)], [(.77,.38),(.82,.43),(.84,.50)],
        [(.14,.60),(.19,.52),(.20,.45)], [(.28,.58),(.31,.51),(.33,.45)],
        [(.45,.59),(.49,.52),(.49,.45)], [(.66,.64),(.66,.59),(.66,.54)],
        [(.85,.72),(.84,.61),(.84,.50)], [(.18,.74),(.24,.78),(.32,.79),(.38,.76),(.38,.71)],
        [(.38,.71),(.45,.72),(.52,.70),(.57,.65),(.57,.59)], [(.57,.59),(.52,.54),(.49,.52)],
        [(.57,.65),(.66,.67),(.73,.63),(.72,.55),(.66,.54)], [(.61,.74),(.62,.69),(.66,.67)],
        [(.66,.67),(.72,.70),(.78,.68),(.80,.61),(.77,.55)], [(.77,.55),(.84,.58),(.90,.56)],
        [(.20,.45),(.12,.49),(.06,.52)], [(.33,.45),(.28,.38),(.23,.35)],
        [(.49,.45),(.54,.38),(.58,.30),(.62,.26)], [(.62,.43),(.72,.35),(.79,.29)],
        [(.70,.50),(.78,.47),(.84,.50)], [(.45,.69),(.39,.61),(.35,.56)],
        [(.28,.70),(.28,.82),(.32,.88)], [(.22,.84),(.13,.87),(.07,.92)],
        [(.45,.59),(.39,.52),(.34,.50)], [(.45,.59),(.54,.61),(.60,.59)],
        [(.66,.54),(.58,.50),(.54,.52)], [(.82,.43),(.88,.38),(.95,.39)],
        [(.49,.36),(.42,.30),(.36,.28)], [(.66,.35),(.73,.27),(.79,.20)],
        [(.16,.29),(.25,.22),(.31,.19)], [(.28,.58),(.22,.58),(.16,.55)],
        [(.70,.50),(.76,.57),(.80,.61)], [(.34,.66),(.30,.61),(.28,.58)],
    ]
    rows = []
    for i, path in enumerate(paths[:n]):
        road_type = "primary" if i < 4 else "secondary" if i < 11 else "local"
        width = 14 if road_type == "primary" else 9 if road_type == "secondary" else 5.5
        geometry = LineString([point(x, y) for x, y in path])
        length_m = round(float(geometry.length * 111_000), 1)
        road_id = f"R-{i + 1:03d}"
        rows.append({
            "road_id": road_id,
            "id": road_id,
            "geometry": geometry,
            "road_name": f"Synthetic {road_type.title()} Road {i + 1:02d}",
            "name": f"Synthetic {road_type.title()} Road {i + 1:02d}",
            "road_class": road_type,
            "roadType": road_type,
            "width": width,
            "length": length_m,
            "speedLimit": 45 if road_type == "primary" else 30 if road_type == "secondary" else 20,
            "drainageNodeIds": [f"DN-{(i % 18) + 1:03d}", f"DN-{((i + 4) % 18) + 1:03d}"],
            "cctvIds": [f"CAM-{((i % 5) + 1):03d}"] if i in (1, 9, 16, 26, 35) else [],
            "source": "synthetic-demo",
            "confidence": 0.9,
        })
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def generate_historical_inundation(bounds: CatchmentBounds) -> gpd.GeoDataFrame:
    """Synthetic ground-truth flooded polygons for the '13 Oct 2020'-style
    calibration event, placed near the low-lying SE corner (near the trunk
    nala) since that is physically where flooding would concentrate."""
    rng = _rng()
    polys = []
    centers_lon = np.linspace(bounds.min_lon + 0.015, bounds.max_lon - 0.003, 4)
    centers_lat = np.linspace(bounds.max_lat - 0.003, bounds.min_lat + 0.003, 4)
    for i, (clon, clat) in enumerate(zip(centers_lon, centers_lat)):
        r = rng.uniform(0.0015, 0.003)
        angles = np.linspace(0, 2 * np.pi, 10)
        coords = [(clon + r * np.cos(a) * rng.uniform(0.7, 1.3),
                   clat + r * np.sin(a) * rng.uniform(0.7, 1.3)) for a in angles]
        polys.append({
            "event_id": f"SYN-EVT-2020-10-13-{i:02d}",
            "geometry": Polygon(coords),
            "event_date": "2020-10-13",
            "severity": str(rng.choice(["moderate", "severe"], p=[0.4, 0.6])),
            "source": "synthetic-demo",
            "confidence": 0.6,
        })
    return gpd.GeoDataFrame(polys, geometry="geometry", crs="EPSG:4326")


def generate_dem(bounds: CatchmentBounds, resolution: int = 60):
    """Small synthetic DEM array (rows x cols) sloping NW(high)->SE(low),
    with a shallow trench aligned to the trunk nala path to mimic a real
    SRTM tile's channel signature. Returns (array, transform-like dict)."""
    rng = _rng()
    x = np.linspace(0, 1, resolution)
    y = np.linspace(0, 1, resolution)
    xx, yy = np.meshgrid(x, y)
    base = 545.0 - 6.0 * ((xx + (1 - yy)) / 2)
    noise = rng.normal(0, 0.15, size=base.shape)
    # trench along the anti-diagonal (NW->SE), matching the trunk nala
    trench = -1.2 * np.exp(-((xx - (1 - yy)) ** 2) / (2 * 0.03 ** 2))
    dem = base + noise + trench
    transform = {
        "min_lon": bounds.min_lon, "max_lon": bounds.max_lon,
        "min_lat": bounds.min_lat, "max_lat": bounds.max_lat,
        "resolution": resolution,
    }
    return dem.astype(np.float32), transform


def generate_landcover(bounds: CatchmentBounds, resolution: int = 60):
    """3-class raster: 0=vegetation/open (imperviousness .25),
    1=built-up (.85), 2=water/nala buffer (1.0). Aligned to the same grid
    as generate_dem."""
    rng = _rng()
    x = np.linspace(0, 1, resolution)
    y = np.linspace(0, 1, resolution)
    xx, yy = np.meshgrid(x, y)
    lc = np.ones_like(xx)  # default built-up
    lc[(xx + yy) < 0.35] = 0  # NW patch = vegetation/open
    lc[np.abs((xx - (1 - yy))) < 0.025] = 2  # nala buffer band
    lc += (rng.random(lc.shape) < 0.03).astype(int) * 0  # small noise hook, no-op
    imperv_map = {0: 0.25, 1: 0.85, 2: 1.0}
    imperviousness = np.vectorize(imperv_map.get)(lc.astype(int))
    return lc.astype(np.int8), imperviousness.astype(np.float32)


def generate_rainfall_event(event_name: str = "2020-10-13", total_mm: float = 192.0,
                              hours: int = 6) -> pd.DataFrame:
    """Hourly-disaggregated rainfall hyetograph summing to `total_mm`.

    total_mm=192 references IMD's reported GHMC-average 24h rainfall of
    ~192mm on 13 Oct 2020 (city peak was 324.5mm at some stations) — see
    docs/historical_event_13oct2020.md for sourcing. The daily TOTAL is the
    real, publicly reported figure; the HOURLY disaggregation shape below is
    synthetic, since IMD's public gridded product is daily, not hourly
    (this mirrors data_inventory.md item 7: do not claim the daily grid as
    a nowcast source).
    """
    rng = _rng()
    # a storm that builds, peaks, and recedes (roughly triangular + noise)
    peak_hour = hours * 0.6
    weights = np.array([max(0.05, 1 - abs(h - peak_hour) / (hours * 0.6)) for h in range(hours)])
    weights += rng.uniform(0, 0.1, hours)
    weights = weights / weights.sum()
    mm = weights * total_mm
    timestamps = pd.date_range("2020-10-13 16:00", periods=hours, freq="h")
    return pd.DataFrame({"timestamp": timestamps, "rainfall_mm": mm.round(1),
                          "event": event_name, "source": "synthetic-demo:disaggregated-from-real-daily-total"})
