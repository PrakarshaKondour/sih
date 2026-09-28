
"""
Real-data ingestion helpers.

REAL mode is intentionally strict: if HYDROLOOP_DATA_MODE=real is selected,
the application does not silently fall back to synthetic layers.

Expected local files:
  data/raw/dem_hyderabad_demo_catchment.tif
  data/raw/hyderabad_roads.geojson
  data/raw/imd_rainfall.csv  (optional for historical calibration)
  data/raw/imerge/*.tif      (optional half-hourly precipitation)

TGRAC download/probing remains available through scripts/fetch_real_data.py.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import box


DATA_DIR = Path(os.environ.get(
    "HYDROLOOP_DATA_DIR", Path(__file__).resolve().parents[3] / "data"
))
RAW = DATA_DIR / "raw"


def _require(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(
            f"Real-data mode requires {label}: {path}. "
            "Run the real-data acquisition script first."
        )
    return path


def load_dem(bounds):
    path = _require(
        RAW / "dem_hyderabad_demo_catchment.tif",
        "Copernicus/SRTM DEM GeoTIFF",
    )
    bbox = box(bounds.min_lon, bounds.min_lat, bounds.max_lon, bounds.max_lat)

    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError("DEM has no CRS")
        geom = gpd.GeoSeries([bbox], crs="EPSG:4326").to_crs(src.crs).iloc[0]
        data, transform = mask(src, [geom], crop=True, filled=True)
        arr = data[0].astype("float64")
        nodata = src.nodata
        if nodata is not None:
            arr[arr == nodata] = np.nan
        if np.isnan(arr).all():
            raise ValueError("DEM crop contains no valid elevation values")

        # Fill edge/no-data values with nearest valid mean for the current
        # prototype. A production implementation should use a hydrologically
        # validated void-fill method.
        fill_value = float(np.nanmedian(arr))
        arr = np.nan_to_num(arr, nan=fill_value)

        h, w = arr.shape
        west, south, east, north = rasterio.transform.array_bounds(
            h, w, transform
        )

    transform_dict = {
        "min_lon": float(west),
        "max_lon": float(east),
        "min_lat": float(south),
        "max_lat": float(north),
        "resolution": max(h, w),
        "rows": h,
        "cols": w,
        "source_crs": str(src.crs),
        "source": "real:copernicus-dem",
    }
    return arr, transform_dict


def load_roads(bounds) -> gpd.GeoDataFrame:
    path = _require(
        RAW / "hyderabad_roads.geojson",
        "real Hyderabad road GeoJSON",
    )
    roads = gpd.read_file(path)
    if roads.empty:
        raise ValueError("Real road layer is empty")

    roads = roads.to_crs("EPSG:4326")
    roads = roads.cx[
        bounds.min_lon:bounds.max_lon,
        bounds.min_lat:bounds.max_lat,
    ].copy()

    if roads.empty:
        raise ValueError("No real roads intersect the configured catchment")

    if "road_id" not in roads.columns:
        roads["road_id"] = [f"REAL-ROAD-{i:06d}" for i in range(len(roads))]
    else:
        roads["road_id"] = roads["road_id"].astype(str)

    if "road_name" not in roads.columns:
        roads["road_name"] = ""
    if "road_class" not in roads.columns:
        roads["road_class"] = "unknown"

    if "source" not in roads or not roads["source"].astype(str).str.startswith("real:").all():
        roads["source"] = "real:road-network"
    roads["confidence"] = 1.0
    return roads[["road_id", "geometry", "road_name", "road_class",
                  "source", "confidence"]].copy()


def load_rainfall_csv(path: Optional[Path] = None):
    import pandas as pd

    path = path or (RAW / "imd_rainfall.csv")
    _require(path, "real rainfall CSV")
    df = pd.read_csv(path)
    required = {"timestamp", "lat", "lon", "rainfall_mm"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Rainfall CSV missing columns: {sorted(missing)}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp")


def latest_imerge_tiffs():
    folder = RAW / "imerg"
    if not folder.exists():
        return []
    return sorted(folder.glob("*.tif"), key=lambda p: p.stat().st_mtime, reverse=True)


def load_observed_rainfall(bounds):
    """Load catchment rainfall from recent IMERG rasters or the IMD CSV.

    IMERG GeoTIFF pixels are treated as precipitation rates in mm/hour and
    converted to 30-minute accumulation before hourly aggregation. The
    resulting series is observed rainfall, not a future precipitation forecast.
    """
    import pandas as pd

    samples = []
    imerg_paths = latest_imerge_tiffs()
    if imerg_paths:
        # File timestamps are preferred; mtime is a fallback for local exports
        # whose names do not retain the IMERG acquisition timestamp.
        def acquisition_time(path: Path):
            match = re.search(r"(20\d{6})[-_]?S(\d{6})", path.name)
            if match:
                return pd.to_datetime(
                    match.group(1) + match.group(2), format="%Y%m%d%H%M%S", utc=True
                )
            return pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC")

        imerg_paths = sorted(imerg_paths, key=acquisition_time)[-8:]
        bbox = box(bounds.min_lon, bounds.min_lat, bounds.max_lon, bounds.max_lat)
        for path in imerg_paths:
            with rasterio.open(path) as src:
                if src.crs is None:
                    continue
                geom = gpd.GeoSeries([bbox], crs="EPSG:4326").to_crs(src.crs).iloc[0]
                pixels, _ = mask(src, [geom], crop=True, filled=False)
                values = np.ma.asarray(pixels[0], dtype="float64")
                valid = values.compressed()
                valid = valid[np.isfinite(valid) & (valid >= 0)]
                if valid.size == 0:
                    continue
                samples.append({
                    "timestamp": acquisition_time(path),
                    "lat": (bounds.min_lat + bounds.max_lat) / 2,
                    "lon": (bounds.min_lon + bounds.max_lon) / 2,
                    "rainfall_mm": float(valid.mean()) * 0.5,
                })
        if samples:
            frame = pd.DataFrame(samples)
            frame["timestamp"] = frame["timestamp"].dt.floor("h")
            hourly = frame.groupby("timestamp", as_index=False).agg(
                lat=("lat", "first"), lon=("lon", "first"), rainfall_mm=("rainfall_mm", "sum")
            )
            return hourly.sort_values("timestamp"), "real:gpm-imerg"

    csv_path = RAW / "imd_rainfall.csv"
    if csv_path.is_file():
        frame = load_rainfall_csv(csv_path)
        frame = frame[
            frame["lat"].between(bounds.min_lat, bounds.max_lat)
            & frame["lon"].between(bounds.min_lon, bounds.max_lon)
        ]
        if not frame.empty:
            frame["timestamp"] = frame["timestamp"].dt.floor("h")
            hourly = frame.groupby("timestamp", as_index=False).agg(
                lat=("lat", "mean"), lon=("lon", "mean"), rainfall_mm=("rainfall_mm", "mean")
            )
            return hourly.sort_values("timestamp"), "real:imd"

    raise FileNotFoundError("No IMERG GeoTIFF or catchment IMD rainfall CSV is available")


def data_status() -> dict:
    dem = RAW / "dem_hyderabad_demo_catchment.tif"
    roads = RAW / "hyderabad_roads.geojson"
    rain = RAW / "imd_rainfall.csv"
    imerg = latest_imerge_tiffs()
    return {
        "mode": os.getenv("HYDROLOOP_DATA_MODE", "synthetic"),
        "dem": {"available": dem.exists(), "path": str(dem)},
        "roads": {"available": roads.exists(), "path": str(roads)},
        "rainfall_csv": {"available": rain.exists(), "path": str(rain)},
        "imerg_tiffs": {"count": len(imerg)},
    }
