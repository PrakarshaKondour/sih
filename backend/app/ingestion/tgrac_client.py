"""
Real adapter for TGRAC's public ArcGIS REST MapServer endpoints.

This code is written against the standard Esri ArcGIS REST API query pattern
(`<MapServer>/<layerId>/query?where=1=1&outFields=*&f=geojson`) that TGRAC's
services expose. It was NOT executed against the live TGRAC server while
building this prototype — the sandbox this repo was built in has no outbound
network path to tgrac.telangana.gov.in — so treat it as "written to spec, not
yet verified against the live schema." The first thing to do when you run
this for real (per data_inventory.md item 15) is:

    python scripts/probe_tgrac_layers.py

which will print the actual layer IDs, field names, and geometry types so you
can fix any field-name mismatches between this file and the live service
before trusting its output.

Usage:
    client = TGRACClient()
    manholes = client.get_layer(TCUR_MAPSERVER, layer_name="Core City Manholes")
"""
from __future__ import annotations
import logging
from typing import Optional
import requests
import geopandas as gpd

logger = logging.getLogger(__name__)

TCUR_MAPSERVER = "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/TCUR_Telangana_Core_Urban_Region_V2/MapServer"
GHMC_NALAS_MAPSERVER = "https://tgrac.telangana.gov.in/arcgis/rest/services/GHMCNalas_Folder/GHMCNalas_vul/MapServer"
MUSI_BASIN_MAPSERVER = "https://tgrac.telangana.gov.in/arcgis/rest/services/MusiRiver/Musi_Basin_Web_Publication_vector/MapServer"


class TGRACClient:
    def __init__(self, timeout_s: int = 30):
        self.timeout_s = timeout_s

    def list_layers(self, mapserver_url: str) -> list[dict]:
        """Return [{id, name, geometryType}, ...] for a MapServer."""
        resp = requests.get(mapserver_url, params={"f": "json"}, timeout=self.timeout_s)
        resp.raise_for_status()
        data = resp.json()
        return [{"id": l["id"], "name": l["name"], "geometryType": l.get("geometryType")}
                for l in data.get("layers", [])]

    def find_layer_id(self, mapserver_url: str, layer_name: str) -> Optional[int]:
        for layer in self.list_layers(mapserver_url):
            if layer["name"].strip().lower() == layer_name.strip().lower():
                return layer["id"]
        return None

    def get_layer(self, mapserver_url: str, layer_name: Optional[str] = None,
                   layer_id: Optional[int] = None, where: str = "1=1",
                   bbox: Optional[tuple[float, float, float, float]] = None
                   ) -> gpd.GeoDataFrame:
        """Fetch a full layer (or bbox subset) as a GeoDataFrame via the
        ArcGIS REST `query` operation, requesting GeoJSON output directly."""
        if layer_id is None:
            if layer_name is None:
                raise ValueError("Provide layer_name or layer_id")
            layer_id = self.find_layer_id(mapserver_url, layer_name)
            if layer_id is None:
                raise ValueError(f"Layer '{layer_name}' not found on {mapserver_url}")

        params = {
            "where": where,
            "outFields": "*",
            "f": "geojson",
            "returnGeometry": "true",
        }
        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            params.update({
                "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
            })

        url = f"{mapserver_url}/{layer_id}/query"
        logger.info("Querying TGRAC layer %s (id=%s)", layer_name, layer_id)
        resp = requests.get(url, params=params, timeout=self.timeout_s)
        resp.raise_for_status()
        gdf = gpd.read_file(resp.text, driver="GeoJSON")
        gdf["source"] = "real:tgrac"
        gdf["confidence"] = 1.0
        return gdf
