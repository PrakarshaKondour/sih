#!/usr/bin/env bash
# Run this on a machine with real outbound internet access to
# tgrac.telangana.gov.in, imdpune.gov.in, and the DEM source below.
# It does NOT run automatically as part of the demo -- the demo uses the
# synthetic-but-schema-matched data in data/demo/ until you run this.
set -e
cd "$(dirname "$0")/.."

echo "== Step 1: probe real TGRAC layer schemas =="
(cd backend && python3 ../scripts/probe_tgrac_layers.py) || \
  echo "  (failed -- likely no network access to tgrac.telangana.gov.in from here)"

echo ""
echo "== Step 2: attempt real DEM download (Copernicus GLO-30) =="
python3 scripts/fetch_dem.py || true

echo ""
echo "== Step 3: IMD gridded rainfall =="
echo "  IMD's gridded rainfall NetCDF requires a manual registration/download"
echo "  from https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html"
echo "  -- this cannot be scripted without credentials. Once downloaded,"
echo "  place the NetCDF file at data/raw/imd_rainfall_0.25deg.nc"

echo ""
echo "Once real layers are fetched, point backend/app/demo/orchestrator.py's"
echo "Catchment class at the real GeoDataFrames/rasters instead of the"
echo "ingestion.synthetic_catchment generators -- every downstream module"
echo "(drainage, hydrology, calibration) consumes the same schemas either way."
