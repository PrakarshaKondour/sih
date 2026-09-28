# HYDROLOOP — Data Inventory

Honesty rule for this whole project: a layer is labeled real only when it was
fetched from its source. This checkout includes a bounded OSM road extract for
the demo catchment. Rainfall, drainage, DEM, land cover, historical labels, and
CCTV remain synthetic as marked in this inventory.

| # | Dataset | Source | URL | Format | CRS | Key fields used | Coverage | Real or synthetic in this repo | How it's used |
|---|---------|--------|-----|--------|-----|------------------|----------|----------------------------------|----------------|
| 1 | Core City Manholes | TGRAC ArcGIS (TCUR_Folder) | `.../TCUR_Telangana_Core_Urban_Region_V2/MapServer` layer "Core City Manholes" | Esri JSON / GeoJSON via REST query | EPSG:4326 (reprojected to EPSG:32644 for computation) | `OBJECTID`, `geometry`, `invert_level` (where present), `ward` | Core Hyderabad | **SYNTHETIC** (schema-matched) — `backend/app/ingestion/tgrac_client.py` implements the real ArcGIS REST query and was written against TGRAC's public `f=json` layer schema, but this sandbox's network egress does not reach `tgrac.telangana.gov.in`, so it could not be executed here | Drainage graph nodes |
| 2 | Core City SewerLine | TGRAC ArcGIS (TCUR_Folder) | same MapServer, layer "Core City SewerLine" | Esri JSON | EPSG:4326→32644 | `geometry`, `diameter_mm` (where present), `material` | Core Hyderabad | **SYNTHETIC** (schema-matched) | Drainage graph edges (piped network) |
| 3 | Hyderabad Roads | OpenStreetMap API; TGRAC alternative | OSM map API for configured bounds; TGRAC TCUR layer | OSM XML → GeoJSON | EPSG:4326→32644 for metric routing | `road_id`, `geometry`, `road_name`, `road_class`, `source` | Selected ~3 km catchment | **REAL OSM extract in this checkout** (`source=real:osm`, ODbL attribution); TGRAC GeoJSON can replace it | Routing graph and flood-risk association |
| 4 | GHMC Nalas / Nala centerline / width | TGRAC ArcGIS (GHMCNalas_Folder) | `.../GHMCNalas_Folder/GHMCNalas_vul/MapServer` layers "Nala", "Present Nalas Centerline", "Existing Width", "Proposed Width", "Segments" | Esri JSON | EPSG:4326→32644 | `geometry`, `existing_width_m`, `proposed_width_m`, `segment_id` | GHMC limits | **SYNTHETIC** (schema-matched) | Drainage graph edges (open-channel network), capacity model |
| 5 | Inundation Areas (historical) | TGRAC ArcGIS (GHMCNalas_Folder) | same MapServer, layer "Inundation Areas" | Esri JSON polygons | EPSG:4326→32644 | `geometry`, `event_date`, `severity` | GHMC limits | **SYNTHETIC** (schema-matched) — used as the historical-label ingestion target | Inverse-calibration ground truth |
| 6 | Musi Basin drainage/roads/land use | TGRAC ArcGIS | `.../MusiRiver/Musi_Basin_Web_Publication_vector/MapServer` | Esri JSON | EPSG:4326→32644 | varies by layer | Musi basin | **NOT FETCHED** — adapter stub present, not required for the single-catchment demo | Optional basin-scale context layer |
| 7 | IMD / GPM IMERG rainfall | IMD CSV / NASA GPM IMERG half-hour GeoTIFF | `data/raw/imd_rainfall.csv`, `data/raw/imerg/*.tif` | CSV / GeoTIFF | EPSG:4326 or raster CRS | `timestamp`, `lat`, `lon`, `rainfall_mm` / raster pixels | Selected catchment | **Not present in this checkout**; the backend can load catchment-filtered IMD CSV or aggregate recent IMERG rasters. Demo rainfall remains synthetic until selected real files are supplied | Rainfall→runoff input |
| 8 | DEM | SRTM 30m (or Copernicus GLO-30) | USGS EarthExplorer / OpenTopography / Copernicus DEM | GeoTIFF | EPSG:4326→32644 | elevation (m) | Selected catchment | **SYNTHETIC** — a small procedurally generated DEM (real Hyderabad terrain is gently sloped ~500–550 m MSL toward the Musi River; the synthetic DEM mimics this gradient and adds nala-aligned depressions) is used so the flow-routing code has something realistic to run on. `scripts/fetch_dem.py` documents the exact real download (SRTM 1-arc-second tile N17E078, ~30 m) and reprojection command a user with network access should run | Flow direction/accumulation, catchment delineation |
| 9 | Land cover / imperviousness | Bhuvan LULC / ESA WorldCover | Bhuvan thematic services | Raster | EPSG:4326→32644 | LULC class → imperviousness % | Selected catchment | **SYNTHETIC** (simple 3-class raster: built-up / vegetation / water, with imperviousness 0.85 / 0.25 / 1.0) | SCS-CN runoff coefficient |
| 10 | Historical flood event: 13 Oct 2020 Hyderabad | News reports, HMDA/GHMC post-event statements, OpenCity flood layer | see `docs/historical_event_13oct2020.md` | manual curation | — | date, approx. affected localities | City-wide, qualitative | **PARTIALLY REAL** — the event and its general severity/date are documented public fact and are used as the *scenario narrative* for "Historical Event Replay" mode; the specific flooded-node ground truth used for calibration metrics is synthetic (no verified geotagged depth dataset was accessible in this environment) | Calibration demo scenario |
| 11 | CCTV frames | GHMC/Hyderabad traffic police public camera feeds (not accessible here) | — | JPEG/MP4 | — | camera_id, lat/lon, timestamp | Demo cameras only | **SYNTHETIC** — procedurally generated demo frames (clear road vs. waterlogged road, simple synthetic images) stored in `data/demo/cctv_frames/` and clearly named `SYNTHETIC_*` | CCTV flood-detection module input |
| 12 | Road/catchment boundary for demo | Derived from synthetic roads/DEM above | — | GeoJSON | EPSG:4326 | — | ~2×2 km demo catchment near Musi River (Hyderabad, ~17.35°N 78.47°E) | **SYNTHETIC**, but coordinates are placed on real Hyderabad geography | Defines the single manageable catchment used end-to-end |

## What "SYNTHETIC" means concretely here
Every synthetic file is written by a generator in `backend/app/ingestion/synthetic_*.py`,
uses the **exact same column/attribute schema** the real TGRAC/IMD adapter would
produce, and every synthetic file on disk is prefixed `SYNTHETIC_` and stamped with
`"source": "synthetic-demo"` in its metadata/attributes so the dashboard can render
it with a visibly different style from real data (per requirement in section 3/16
of the spec: real vs. inferred/synthetic must be visually distinguishable).

## What IS real
- The bounded OpenStreetMap road extract in `data/raw/hyderabad_roads.geojson`
  used by the active demo routing graph, attributed to OpenStreetMap contributors.
- The Hyderabad location, general terrain gradient, and Musi River orientation used
  to place the synthetic catchment.
- The 13 Oct 2020 flood event date/general severity (used only as narrative context).
- TGRAC and IMD adapter code paths exist, but were not verified against live
  source schemas in this task.

## Next steps for this catchment
The OSM road extract is already active. Add a verified real DEM and IMERG or
IMD observations using `REAL_DATA_SETUP.md`; acquire and schema-map real TGRAC
drainage before labeling nala/manhole layers real. Downstream routing consumes
the road contract, and rainfall uses the same runoff/simulation pipeline for
real observations or the explicit synthetic demo mode.


## Prototype source behavior
The active `data/raw/hyderabad_roads.geojson` is real OSM data for the one
small catchment. The `real` rainfall option selects IMERG first, then a
catchment-filtered IMD CSV; neither is present in this checkout, so demo
rainfall is explicitly selected. `HYDROLOOP_DATA_MODE=real` additionally
requires a real DEM and roads and never falls back for those layers.
Drainage, land cover, historical inundation, and CCTV remain synthetic;
DEM-derived flowpaths are separately tagged **INFERRED**. Backend nowcast
horizons and road risks are shared with routing and alerts.
