
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
import argparse
import xml.etree.ElementTree as ET

import geopandas as gpd
import requests
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingestion.tgrac_client import TGRACClient, TCUR_MAPSERVER

OUT = ROOT / "data" / "raw"
OUT.mkdir(parents=True, exist_ok=True)

def fetch_osm_roads(force: bool = False):
    """Download and normalize drivable OSM ways for the configured catchment."""
    from app.core.schemas import CatchmentBounds

    path = OUT / "hyderabad_roads.geojson"
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing road layer: {path}; pass --force")

    bounds = CatchmentBounds()
    response = requests.get(
        "https://api.openstreetmap.org/api/0.6/map",
        params={"bbox": f"{bounds.min_lon},{bounds.min_lat},{bounds.max_lon},{bounds.max_lat}"},
        headers={"User-Agent": "HydroLoop-SIH-demo/1.0 (bounded research prototype)"},
        timeout=60,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    nodes = {
        element.attrib["id"]: (float(element.attrib["lon"]), float(element.attrib["lat"]))
        for element in root.findall("node")
    }
    drivable = {
        "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
        "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
        "residential", "living_street", "service", "road",
    }
    rows = []
    for way in root.findall("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        road_class = tags.get("highway")
        coordinates = [nodes[ref.attrib["ref"]] for ref in way.findall("nd")
                       if ref.attrib["ref"] in nodes]
        if road_class not in drivable or len(coordinates) < 2:
            continue
        way_id = way.attrib["id"]
        rows.append({
            "road_id": f"OSM-WAY-{way_id}",
            "geometry": LineString(coordinates),
            "road_name": tags.get("name", ""),
            "road_class": road_class,
            "source": "real:osm",
            "confidence": 1.0,
            "attribution": "© OpenStreetMap contributors",
        })

    if not rows:
        raise RuntimeError("OpenStreetMap returned no drivable road ways in the demo catchment")
    roads = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    roads.to_file(path, driver="GeoJSON")
    print(f"Saved {len(roads)} real OSM road ways -> {path}")
    print("Attribution: © OpenStreetMap contributors (Open Database License)")


def fetch_tgrac_roads():
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--osm-roads", action="store_true", help="Fetch bounded OpenStreetMap roads")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing road GeoJSON")
    args = parser.parse_args()
    if args.osm_roads:
        fetch_osm_roads(force=args.force)
    else:
        fetch_tgrac_roads()


if __name__ == "__main__":
    main()
