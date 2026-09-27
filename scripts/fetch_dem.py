#!/usr/bin/env python3
"""
Documents (and, on a machine with real internet access, performs) the real
DEM download for the demo catchment, replacing
ingestion.synthetic_catchment.generate_dem().

The demo catchment (see core/schemas.CatchmentBounds) sits inside SRTM
tile N17E078 (Hyderabad, Telangana). Two real, free, no-API-key sources:

  1. OpenTopography global DEM API (SRTM GL1, 30m):
     https://portal.opentopography.org/API/globaldem?demtype=SRTMGL1&
         south=17.365&north=17.385&west=78.455&east=78.485&
         outputFormat=GTiff
     (OpenTopography does require a free API key for the REST API as of
     2024+; sign up at https://opentopography.org/)

  2. Copernicus GLO-30 DEM (ESA, via AWS Open Data, no key required):
     https://copernicus-dem-30m.s3.amazonaws.com/
     Tile naming: Copernicus_DSM_COG_10_N17_00_E078_00_DEM/...tif

This script attempts (2) since it needs no API key, and writes
data/raw/dem_hyderabad_demo_catchment.tif. If it can't reach the source
(e.g. this sandbox has no network path to AWS S3 either), it explains
that and points back to the synthetic DEM already in use.

Usage:
    python scripts/fetch_dem.py
"""
import os
import sys

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "raw",
                          "dem_hyderabad_demo_catchment.tif")

COPERNICUS_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_N17_00_E078_00_DEM/"
    "Copernicus_DSM_COG_10_N17_00_E078_00_DEM.tif"
)


def main():
    try:
        import requests
    except ImportError:
        print("requests not installed; pip install requests", file=sys.stderr)
        sys.exit(1)

    print(f"Attempting real Copernicus GLO-30 DEM download:\n  {COPERNICUS_URL}")
    try:
        resp = requests.get(COPERNICUS_URL, timeout=30, stream=True)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        with open(OUT_PATH, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
        print(f"Saved real DEM tile to {OUT_PATH}")
        print("Next: crop it to CatchmentBounds with rasterio and pass the "
              "resulting array into hydrology/dem_processing.py in place of "
              "ingestion.synthetic_catchment.generate_dem().")
    except Exception as e:
        print(f"\nCould not download the real DEM from this machine: {e}")
        print("This is expected in a sandboxed/offline environment. The "
              "pipeline is currently using the synthetic DEM from "
              "ingestion.synthetic_catchment.generate_dem() instead -- see "
              "data_inventory.md item 8.")
        sys.exit(0)


if __name__ == "__main__":
    main()
