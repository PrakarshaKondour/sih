# HYDROLOOP — Data Inventory

Honesty rule for this whole project: nothing in the running demo is labeled "real"
unless it was actually fetched from the source below. Where the sandbox this
prototype was built in had no network path to a source, that source's **adapter
code is real and will work on a machine with internet access**, but the data
shipped in `data/demo/` is **synthetic, schema-matched** to what the adapter
would return.

| # | Dataset | Source | URL | Format | CRS | Key fields used | Coverage | Real or synthetic in this repo | How it's used |
|---|---------|--------|-----|--------|-----|------------------|----------|----------------------------------|----------------|
| 1 | Core City Manholes | TGRAC ArcGIS (TCUR_Folder) | `.../TCUR_Telangana_Core_Urban_Region_V2/MapServer` layer "Core City Manholes" | Esri JSON / GeoJSON via REST query | EPSG:4326 (reprojected to EPSG:32644 for computation) | `OBJECTID`, `geometry`, `invert_level` (where present), `ward` | Core Hyderabad | **SYNTHETIC** (schema-matched) — `backend/app/ingestion/tgrac_client.py` implements the real ArcGIS REST query and was written against TGRAC's public `f=json` layer schema, but this sandbox's network egress does not reach `tgrac.telangana.gov.in`, so it could not be executed here | Drainage graph nodes |
| 2 | Core City SewerLine | TGRAC ArcGIS (TCUR_Folder) | same MapServer, layer "Core City SewerLine" | Esri JSON | EPSG:4326→32644 | `geometry`, `diameter_mm` (where present), `material` | Core Hyderabad | **SYNTHETIC** (schema-matched) | Drainage graph edges (piped network) |
| 3 | Hyderabad Roads | TGRAC ArcGIS (TCUR_Folder) | same MapServer, layer "Hyderabad Roads" | Esri JSON | EPSG:4326→32644 | `geometry`, `road_name`, `road_class` | Core Hyderabad | **SYNTHETIC** (schema-matched); OSM fallback adapter also implemented | Routing graph, imperviousness proxy |
| 4 | GHMC Nalas / Nala centerline / width | TGRAC ArcGIS (GHMCNalas_Folder) | `.../GHMCNalas_Folder/GHMCNalas_vul/MapServer` layers "Nala", "Present Nalas Centerline", "Existing Width", "Proposed Width", "Segments" | Esri JSON | EPSG:4326→32644 | `geometry`, `existing_width_m`, `proposed_width_m`, `segment_id` | GHMC limits | **SYNTHETIC** (schema-matched) | Drainage graph edges (open-channel network), capacity model |
| 5 | Inundation Areas (historical) | TGRAC ArcGIS (GHMCNalas_Folder) | same MapServer, layer "Inundation Areas" | Esri JSON polygons | EPSG:4326→32644 | `geometry`, `event_date`, `severity` | GHMC limits | **SYNTHETIC** (schema-matched) — used as the historical-label ingestion target | Inverse-calibration ground truth |
| 6 | Musi Basin drainage/roads/land use | TGRAC ArcGIS | `.../MusiRiver/Musi_Basin_Web_Publication_vector/MapServer` | Esri JSON | EPSG:4326→32644 | varies by layer | Musi basin | **NOT FETCHED** — adapter stub present, not required for the single-catchment demo | Optional basin-scale context layer |
| 7 | IMD gridded rainfall (0.25°) | IMD Pune | `imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html` | NetCDF, daily, 0.25° grid | EPSG:4326 | `RAINFALL`, `TIME`, `LATITUDE`, `LONGITUDE` | All-India, 0.25° | **SYNTHETIC** (schema-matched, hourly-disaggregated) — this grid is explicitly a *daily historical* product, not a nowcast feed. It is used here only for **historical-event calibration**, never presented as a live/high-frequency source. A `RainfallAdapter` interface is provided so a real live source (IMD AWS API, radar, or GPM IMERG) can be plugged in without changing the pipeline | Rainfall→runoff input for calibration events |
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
- The Hyderabad location, general terrain gradient, and Musi River orientation used
  to place the synthetic catchment.
- The 13 Oct 2020 flood event date/general severity (used only as narrative context).
- The ArcGIS REST / NetCDF adapter code paths — these are real, tested against the
  documented public schemas of these services, and will pull real data the moment
  they run somewhere with network access to `tgrac.telangana.gov.in` and
  `imdpune.gov.in`.

## Next step to make this fully real
Run `scripts/fetch_real_data.sh` from a machine with outbound internet access. It
calls the same adapters used here, writes into `data/raw/`, and the rest of the
pipeline (`backend/app/drainage`, `hydrology`, `calibration`) is unchanged — it does
not know or care whether its GeoDataFrame came from TGRAC or from the synthetic
generator, because both are validated against the same `schemas.py` contracts.
