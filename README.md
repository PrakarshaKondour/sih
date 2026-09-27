# HYDROLOOP

Urban Flood Nowcasting System (Drainage and Rainfall Coupling) — prototype
for SIH PS 26085.

**Read `data_inventory.md` and `docs/limitations.md` before treating any
number this produces as ground truth.** This prototype runs on
schema-matched **synthetic demo data** by default (real TGRAC/IMD/DEM
adapters are implemented but couldn't be executed from the sandbox this
was built in — see below). Every layer the API and map serve is tagged
`source: real:*` / `synthetic-demo` / `inferred:*` so you always know
which is which.

## What this is

Rainfall → SCS-CN runoff → DEM flow routing → real-schema drainage graph
(manholes/sewer/nala) → Manning's-equation capacity → 0–3h flood nowcast →
CCTV-based data assimilation → flood-aware routing → mock multilingual
alerts, plus inverse capacity calibration against a historical event
(13 Oct 2020 Hyderabad floods) via `scipy.optimize`.

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

## 2. Exact command to run the demo
With the backend running (either option above):
- Open http://localhost:5173 (or http://localhost:80 if using Docker) and
  click **"Run Flood Scenario"** or **"Historical Event Replay"** in the
  left panel.
- Or hit the API directly:
  ```bash
  curl http://localhost:8000/api/scenario/run
  curl http://localhost:8000/api/scenario/historical-replay
  ```
- Or run the progressive experiment ladder (spec section 13) from the CLI,
  no server needed:
  ```bash
  cd hydroloop/backend
  python3 ../scripts/run_experiments.py
  ```
- Run the test suite:
  ```bash
  cd hydroloop
  python3 -m pytest tests/ -v   # 14/14 passing as of this build
  ```

## 3. Data inventory
See `data_inventory.md` — full per-layer table of source, format, CRS,
fields, and **real vs. synthetic** status. Short version: the TGRAC
ArcGIS / IMD NetCDF adapters are written and will work with real network
access (`backend/app/ingestion/tgrac_client.py`), but this sandbox
couldn't reach `tgrac.telangana.gov.in` / `imdpune.gov.in`, so the running
demo uses `backend/app/ingestion/synthetic_catchment.py`, which generates
schema-matched synthetic data on real Hyderabad geography. Run
`scripts/fetch_real_data.sh` on a machine with internet access to attempt
the real pull.

## 4. What is real vs. synthetic
- **Real:** the Hyderabad location/terrain gradient used, the 13 Oct 2020
  flood event's date and reported rainfall totals (192mm GHMC average /
  324.5mm peak, sourced — see `docs/historical_event_13oct2020.md`), every
  engineering formula (SCS-CN, Manning's, D8 flow routing), and the
  TGRAC/IMD adapter code paths (untested live, see above).
- **Synthetic:** the entire drainage network, roads, historical-flood
  ground-truth polygons, DEM, land cover, and CCTV frames — all schema-
  matched to what real TGRAC/IMD/SRTM data would look like, all tagged
  `source="synthetic-demo"` in the API/map.

## 5. What's implemented vs. approximate
**Implemented and tested** (14 automated tests, `tests/test_pipeline.py`):
SCS-CN runoff, D8 flow direction/accumulation/sink-fill, Manning's-equation
pipe & trapezoidal-channel capacity, drainage-graph construction with
spatial snapping + DEM-inferred low-point edges, mass-balance flood
simulation, 0/15/30/60/120/180-min nowcast with configurable risk levels,
inverse capacity calibration via `scipy.optimize` (Nelder-Mead) with
before/after metrics, a second-event holdout-transfer check, a heuristic
CCTV flood detector + confidence-weighted assimilation, flood-aware
routing (NORMAL/AMBULANCE/FIRE profiles) on an intersection-aware road
graph, and a mock bilingual (English/Telugu) alert service that never
dispatches without real provider credentials.

**Approximate / simplified** — full list with reasoning in
`docs/limitations.md`: no full St. Venant hydraulics (auditable
mass-balance routing instead), cell→nearest-node runoff assignment instead
of full flow-path routing, ponding depth from an assumed
contributing-area fraction rather than surveyed depression storage,
free-draining boundary at catchment exits (no river backwater), one
capacity-multiplier parameter per edge *type* rather than per edge, and a
hand-written (not trained) CCTV heuristic.

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
Full list in `docs/limitations.md`. Headline items: real data sources
haven't been fetched live yet (network-restricted build environment), only
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
  tests/             pytest suite (14 tests)
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

The current catchment remains the repository's clearly-labelled `synthetic-demo` road/drainage/DEM data. The TGRAC/DEM adapter paths remain separate so verified real datasets can be plugged into the same contracts later. Do not describe the current synthetic road/flood layer as live navigation or observed flooding.
