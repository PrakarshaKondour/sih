"""
Data contracts shared by every ingestion adapter (real or synthetic).

The point of this file: `backend/app/drainage`, `hydrology`, and `calibration`
never import from `ingestion` directly. They only consume GeoDataFrames /
dicts that satisfy these column contracts. That is what lets a real TGRAC
GeoDataFrame and a synthetic one be dropped into the same pipeline
interchangeably, and it's how the dashboard knows how to color "real" vs
"inferred/synthetic" features without special-casing each dataset.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional

SourceTag = Literal["real:tgrac", "real:imd", "real:srtm", "real:bhuvan",
                     "inferred:dem", "synthetic-demo"]

# Required columns for each layer type. Any adapter (real or synthetic) MUST
# produce a GeoDataFrame with at least these columns, plus a `source` column
# using one of the SourceTag values above and a `confidence` float in [0, 1].

MANHOLE_SCHEMA = ["node_id", "geometry", "elevation_m", "invert_level_m",
                   "node_type", "source", "confidence"]

SEWERLINE_SCHEMA = ["edge_id", "geometry", "from_node", "to_node",
                     "diameter_mm", "slope", "material", "edge_type",
                     "source", "confidence"]

NALA_SCHEMA = ["edge_id", "geometry", "from_node", "to_node",
               "existing_width_m", "proposed_width_m", "depth_m",
               "edge_type", "source", "confidence"]

ROAD_SCHEMA = ["road_id", "geometry", "road_name", "road_class",
               "source", "confidence"]

INUNDATION_SCHEMA = ["event_id", "geometry", "event_date", "severity",
                      "source", "confidence"]

RAINFALL_SCHEMA = ["timestamp", "lat", "lon", "rainfall_mm"]

CCTV_FRAME_SCHEMA = ["camera_id", "lat", "lon", "timestamp", "frame_path", "source"]


@dataclass
class CatchmentBounds:
    """Bounding box of the single manageable demo catchment.

    Placed on real Hyderabad geography near the Musi River (per spec section
    15: "identify one manageable Hyderabad catchment"). ~2.2km x 1.8km.
    """
    min_lon: float = 78.455
    max_lon: float = 78.485
    min_lat: float = 17.365
    max_lat: float = 17.385
    name: str = "Musi-adjacent demo catchment (Hyderabad core)"
    crs: str = "EPSG:4326"
    projected_crs: str = "EPSG:32644"  # UTM 44N, used for all metric computation


@dataclass
class RiskThresholds:
    """Configurable, NOT scientifically validated. See docs/limitations.md."""
    low_m: float = 0.05
    moderate_m: float = 0.15
    high_m: float = 0.30
    critical_m: float = 0.50
    validated: bool = False
