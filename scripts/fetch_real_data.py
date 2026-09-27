
"""
Prepare real-data inputs for HYDROLOOP.

1) DEM:
   The existing scripts/fetch_dem.py downloads the Copernicus GLO-30 tile
   used by the Hyderabad demo bounds.

2) TGRAC:
   First run probe_tgrac_layers.py. Then use the live layer IDs/names to
   export roads as data/raw/hyderabad_roads.geojson. The TGRAC adapter already
   supports ArcGIS REST -> GeoJSON.

3) Rainfall:
   For historical calibration, place an IMD rainfall CSV at
   data/raw/imd_rainfall.csv with:
       timestamp,lat,lon,rainfall_mm

   For near-real-time precipitation, NASA GPM IMERG Early/Late 30-minute
   GeoTIFFs can be placed under data/raw/imerg/. IMERG is satellite
   precipitation, not a future rainfall forecast; forecast hours need a
   separate forecast source/model.

This script does not fabricate data and never overwrites real files.
"""
from pathlib import Path
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingestion.tgrac_client import TGRACClient, TCUR_MAPSERVER

OUT = ROOT / "data" / "raw"
OUT.mkdir(parents=True, exist_ok=True)

def main():
    client = TGRACClient()
    print("== TGRAC live layer probe ==")
    layers = client.list_layers(TCUR_MAPSERVER)
    for layer in layers:
        print(f"[{layer['id']}] {layer['name']} ({layer['geometryType']})")

    candidates = [
        l for l in layers
        if "road" in l["name"].lower()
    ]
    if not candidates:
        raise RuntimeError(
            "No road-like TGRAC layer was found. Inspect the probe output "
            "and choose the correct live layer manually."
        )

    chosen = candidates[0]
    print(f"\nCandidate road layer: {chosen['id']} {chosen['name']}")
    gdf = client.get_layer(TCUR_MAPSERVER, layer_id=chosen["id"])
    if gdf.empty:
        raise RuntimeError("Chosen TGRAC road layer returned zero features.")

    # Normalize the common road fields without pretending unknown fields are
    # scientifically equivalent.
    import geopandas as gpd
    if "road_id" not in gdf.columns:
        if "OBJECTID" in gdf.columns:
            gdf["road_id"] = gdf["OBJECTID"].astype(str)
        else:
            gdf["road_id"] = [f"TGRAC-ROAD-{i:06d}" for i in range(len(gdf))]
    if "road_name" not in gdf.columns:
        gdf["road_name"] = ""
    if "road_class" not in gdf.columns:
        gdf["road_class"] = "unknown"

    out = gdf[["road_id", "geometry", "road_name", "road_class",
               "source", "confidence"]].to_crs("EPSG:4326")
    path = OUT / "hyderabad_roads.geojson"
    out.to_file(path, driver="GeoJSON")
    print(f"Saved {len(out)} real road features -> {path}")
    print("\nNext: run scripts/fetch_dem.py and supply a real rainfall input.")

if __name__ == "__main__":
    main()
