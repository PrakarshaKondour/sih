# HYDROLOOP

Urban Flood Nowcasting System (Drainage and Rainfall Coupling) — prototype
for SIH PS 26085.

**Read `data_inventory.md` and `docs/limitations.md` before treating any
number this produces as ground truth.** This checkout includes a bounded
OpenStreetMap road extract for the demo catchment. Rainfall defaults to the
explicit synthetic demo; drainage, DEM, land cover, and CCTV samples are
synthetic. DEM-derived links are tagged **INFERRED**. Real IMERG/IMD rainfall
can be selected when its input files are supplied.

## What this is

Rainfall → SCS-CN runoff → DEM flow routing → drainage graph →
Manning's-equation capacity → backend-owned 0–3h flood/road-risk nowcast →
sample CCTV verification → flood-aware routing → bilingual threshold alert.
The dashboard displays backend scenario outputs; it does not calculate a
second flood state.

## 1. Exact setup commands

### Option A — Docker Compose (recommended)
```bash
cd hydroloop
docker compose up --build
```
- Backend: http://localhost:8000 (docs at http://localhost:8000/docs)
- Frontend: http://localhost:5173

### Option B — run locally
```bash
# backend
cd hydroloop/backend
python3 -m venv .venv && source .venv/bin/activate   # or: pip install --break-system-packages -r requirements.txt
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# frontend (separate terminal)
cd hydroloop/frontend
npm install
npm run dev
# open http://localhost:5173 (vite dev server proxies /api -> localhost:8000)
```

## 2. Run the demo
With Docker running, open http://localhost:5173 and choose **Demo rainfall**.
The initial backend scenario loads automatically. Choose **Real observations**
when IMERG GeoTIFFs or a catchment IMD CSV are present.

The backend also exposes:

```text
GET /api/scenario/run?rainfall_mode=demo
GET /api/scenario/run?rainfall_mode=real
GET /api/catchment/layers
POST /api/routing/route
```

## 3. Data provenance
The active road layer is a real OSM extract (`source=real:osm`) limited to
the small Hyderabad catchment; © OpenStreetMap contributors, ODbL. Real
TGRAC roads can replace it. Rainfall remains synthetic in this checkout
because there are no IMERG/IMD observation files. Drainage, DEM, land cover,
historical flood labels, and CCTV are synthetic. DEM-derived drainage links
are **INFERRED**, not real nala observations. See `data_inventory.md` and
`REAL_DATA_SETUP.md` for details and acquisition steps.

## 4. Backend-owned outputs
Rainfall enters the SCS-CN runoff and drainage simulation once. Backend
outputs provide the T+0/T+15/T+30/T+60/T+120/T+180 flood depths, hotspots,
drainage utilization, severity, and per-road risk. Routing consumes those
same road IDs and risks. Sample CCTV detections are linked to their nearest
road and can raise its risk. HIGH/CRITICAL forecast thresholds create
English and Telugu alerts; an optional `HYDROLOOP_ALERT_WEBHOOK_URL` sends
them via a simple JSON POST. SMS/IVR are not implemented.

## 5. Validation and limitations
Run the suite with `python -m pytest tests -v`; the current suite checks
runoff, drainage, nowcast, CCTV, API routing, and bilingual alert contracts.
The hydraulic core is a mass-balance approximation, runoff is assigned to
nearest drainage nodes, 15/30-minute states are interpolated, and CCTV is
an untrained heuristic. Forecast skill is not validated against verified
local flood depths; see `docs/limitations.md`.

## 6. Actual evaluation results
`docs/experiment_results.md` (raw numbers in `docs/experiment_results.json`,
regenerate with `scripts/run_experiments.py`) — progressive A→D experiment
ladder against the 13 Oct 2020 profile, **all explicitly labeled SYNTHETIC
VALIDATION** since ground truth is synthetic:

| Experiment | Precision | Recall | F1 |
|---|---|---|---|
| A. Rainfall only | 0.146 | 1.000 | 0.255 |
| B. + DEM (infinite capacity) | 0.158 | 0.500 | 0.240 |
| C. + drainage network (capacity-constrained) | 0.160 | 1.000 | 0.276 |
| D. + inverse calibration | 0.160 | 1.000 | 0.276 |

The calibration *mechanism* moves capacity factors away from their priors
in every run (see `experiment_results.json`), but at this record-storm
intensity the classification outcome is largely capacity-insensitive — see
`docs/experiment_results.md` for the honest explanation, including a
milder-rainfall case where calibration visibly improves F1 (0.269→0.312).

## 7. Remaining limitations
Full list in `docs/limitations.md`. Headline items: rainfall, drainage,
DEM, and land cover are synthetic in this checkout; only
one real-referenced historical event exists for calibration (no true
multi-event holdout), the CCTV detector is an untrained heuristic, and the
hydraulic core is a mass-balance approximation rather than a full solver.

## 8. Next steps for scaling from one catchment to Hyderabad
1. Run `scripts/fetch_real_data.sh` for real TGRAC/DEM data; verify field
   names with `scripts/probe_tgrac_layers.py` first.
2. Source 2-3 more independently verified historical flood events for
   real event-based holdout validation (`calibration/event_validation.py`
   is ready for this — it just needs real data).
3. Replace nearest-node runoff assignment with full DEM flow-path routing
   once the catchment (and therefore grid) gets large enough that the
   simplification's error becomes material.
4. Move from the in-memory `Catchment` singleton
   (`backend/app/demo/orchestrator.py`) to PostGIS-backed persistence (a
   `db` service is already defined in `docker-compose.yml` behind the
   `with-db` profile) so the network doesn't rebuild on every restart and
   multiple catchments/tiles can be served.
5. Tile the DEM/hydrology processing (currently a single 60x60 in-memory
   grid) for city-scale coverage, and parallelize the per-catchment
   simulation.
6. Replace the CCTV heuristic with a trained model once labeled real
   flood-CCTV footage is available; wire real GHMC/traffic-police camera
   feeds in place of the 3 synthetic demo cameras.
7. Swap the mass-balance hydraulic core for EPA SWMM once real network +
   calibration data justify the added complexity (spec's originally
   suggested tool for this piece).

## Repository layout
```
hydroloop/
  backend/app/
    core/          shared schemas/contracts (real vs synthetic tagging)
    ingestion/      real TGRAC client + synthetic data generators
    hydrology/      DEM processing, SCS-CN runoff, mass-balance simulation, nowcast
    drainage/       graph builder, Manning's-equation capacity model
    calibration/    inverse calibration (scipy.optimize) + event validation
    cctv/           synthetic demo frames + heuristic detector + assimilation
    routing/        flood-aware routing
    alerts/         mock bilingual SMS/IVR
    demo/           orchestrator tying it all together (the two demo modes)
    api/            FastAPI routes
  frontend/src/      React + TypeScript + Leaflet dashboard
  data/demo/         synthetic CCTV frames generated at startup
  scripts/           real-data fetch helpers, experiment runner
  tests/             pytest suite
  docs/              historical event sourcing, limitations, experiment results
  data_inventory.md  full data provenance table
```

## 9. Flood-safe navigation added

The dashboard now supports a navigation workflow on top of the existing flood-aware routing engine:

1. Use browser GPS or click a start point.
2. Click a destination.
3. Select a forecast horizon (0/15/30/60/120/180 minutes).
4. Select a routing profile (normal, ambulance, fire/emergency).
5. The backend compares the shortest route with the flood-risk-aware route.
6. Road IDs carrying HIGH/CRITICAL risk on the normal route are returned.
7. CRITICAL roads are treated as effectively impassable by the routing cost and are exposed as `blocked_road_ids`.
8. The map highlights at-risk roads and shows the normal route and flood-aware alternate route separately.

Road routing uses the bounded OSM road extract in this checkout and the
backend-computed risk for each OSM way ID. Sample CCTV detections are
explicitly labeled and can raise the associated road's risk. These are
prototype results, not live traffic or a validated public warning.
