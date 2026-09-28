
# Real-data mode

The prototype can now run in a **strict real-data mode**.

## 1. Create the real DEM

```bat
cd backend
python ..\scripts\fetch_dem.py
```

The existing script downloads the Hyderabad-area Copernicus DEM GLO-30 tile.
Copernicus also provides global GLO-90 coverage.

## 2. Get the real Hyderabad road network

For the small demo catchment, use the bounded OSM map endpoint:

```powershell
python scripts/fetch_real_data.py --osm-roads
```

It keeps drivable road classes and writes `source=real:osm`, way IDs, and
OpenStreetMap attribution to `data/raw/hyderabad_roads.geojson`. It refuses
to overwrite an existing file unless `--force` is supplied.

Alternatively, fetch a verified TGRAC layer as described below.

Run:

```bat
cd backend
python ..\scripts\fetch_real_data.py
```

This probes the live TGRAC ArcGIS service, identifies road-like layers, and
exports the selected live layer to:

```text
data/raw/hyderabad_roads.geojson
```

**Check the printed layer list before trusting the selected layer.** If the
service exposes several road-like layers, change the candidate selection in
`scripts/fetch_real_data.py` to the verified layer ID.

## 3. Rainfall

### Historical IMD
Prepare:

```text
data/raw/imd_rainfall.csv
```

with:

```text
timestamp,lat,lon,rainfall_mm
```

The existing IMD 0.25-degree gridded rainfall product is suitable for
historical calibration, but it is not a high-frequency nowcast feed.

### Near-real-time precipitation
Use NASA GPM IMERG Early/Late 30-minute products and place the downloaded
GeoTIFFs in:

```text
data/raw/imerg/
```

These are observations/precipitation estimates. They are not a future
rainfall forecast, so a 0–6 hour forecast still needs a forecast source or
forecast model.

## 4. Enable strict real mode

Windows CMD:

```bat
set HYDROLOOP_DATA_MODE=real
```

PowerShell:

```powershell
$env:HYDROLOOP_DATA_MODE="real"
```

Then restart:

```bat
uvicorn app.main:app --reload --port 8000
```

If a required real file is missing, the backend fails clearly instead of
silently using synthetic data.

Rainfall mode is configured separately because real rainfall observations
do not imply that roads, DEM, or drainage are real. Select **Real
observations** in the dashboard or set:

```powershell
$env:HYDROLOOP_RAINFALL_MODE="real"
```

The rainfall endpoint and dashboard use IMERG GeoTIFFs first, then the
catchment-filtered IMD CSV. With no usable input, the API returns an explicit
unavailable-data error; it does not substitute demo rainfall in real mode.

## Current boundary

Real roads and real DEM are supported in strict mode.

The drainage network, land-cover raster, historical inundation labels and
CCTV feeds remain synthetic in this prototype. DEM-derived drainage links
are labeled INFERRED. Use the dashboard's explicit demo-rainfall selection
for the synthetic fallback.

## Optional alert webhook

Set `HYDROLOOP_ALERT_WEBHOOK_URL` to a receiver that accepts JSON POSTs.
When the backend forecast crosses the HIGH flood threshold, it sends one
payload with English and Telugu message strings plus severity and horizon.
Without a URL, the alert is displayed in the dashboard and marked MOCK; no
SMS/IVR provider integration is included.
