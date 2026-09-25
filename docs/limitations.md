# Limitations, assumptions, and what's real vs. approximate

This is the single honest accounting file spec section 16 (and the final
"remaining limitations" deliverable) asks for. Read alongside
`data_inventory.md` (data provenance) and
`docs/historical_event_13oct2020.md` (the one historical event used).

## What is real
- The location, bounding box, and general terrain gradient/river
  orientation of the demo catchment (real Hyderabad geography).
- The 13 Oct 2020 flood event's date, general severity, and the reported
  rainfall totals used to drive the historical-replay scenario (192mm
  GHMC average / 324.5mm peak — sourced, see
  `docs/historical_event_13oct2020.md`).
- The TGRAC ArcGIS REST and IMD adapter *code paths* — written to the
  documented public schema, not yet executed against the live services
  from this environment (no network path to `tgrac.telangana.gov.in` /
  `imdpune.gov.in` here).
- Every engineering formula used (SCS Curve Number runoff, Manning's
  equation for pipes and trapezoidal channels, D8 flow routing) is a
  standard, textbook method, not invented.

## What is synthetic (and clearly labeled as such in the data + API)
- The entire drainage network (manholes, sewer lines, nala, roads,
  historical-flood polygons, DEM, land cover, CCTV frames) — see
  `data_inventory.md` for the full per-layer table.
- Every synthetic feature is tagged `source="synthetic-demo"` (or
  `source="inferred:dem"` for DEM-derived low-point connections) and a
  `confidence` field, and the frontend is expected to render these
  visually distinct from `source="real:*"` features (spec section 3/11).

## Simplifications in the physics (documented, not hidden)
1. **No dynamic-wave / St. Venant hydraulics.** The simulation is a
   timestep-wise mass-balance / reservoir-routing model (rainfall→runoff→
   node inflow→capacity-limited downstream routing→retained-volume→depth
   via a ponding-area divisor), not a full 1D/2D hydraulic solver. This
   was a deliberate choice per spec section 4 ("do not use ML just for
   the sake of using ML") favoring an auditable method over either a
   black-box model or a much heavier hydraulic engine, appropriate for a
   single small demo catchment. A production system should use EPA SWMM
   or a comparable solver for the piped/open-channel hydraulics.
2. **Cell→nearest-node runoff assignment.** Each DEM cell's runoff is
   assigned to its single nearest drainage-graph node by straight-line
   distance, not routed cell-by-cell along the DEM flow-accumulation
   path to the network. This is fast and workable at demo scale (60x60
   grid, ~80 nodes) but is a real approximation — see
   `hydrology/simulation.py` module docstring.
3. **Ponding-area assumption.** Local flood depth = retained volume /
   assumed ponding area. Ponding area is estimated as a fixed fraction
   (`PONDING_AREA_FRACTION = 0.08`, documented in `simulation.py`) of each
   node's DEM-contributing catchment area — not a measured or surveyed
   local depression storage volume. This single assumption has an
   outsized effect on absolute depth numbers; treat depths as
   order-of-magnitude / relative-risk indicators, not survey-grade
   predictions.
4. **Boundary condition at network exits.** Nodes where the trunk nala or
   an outfall connector leaves the catchment (no outgoing edge, node id
   prefixed `OUTFALL-` or `SYN-NALA-NODE-`) are treated as free-draining
   (no backwater from the Musi River is modeled). A real river in flood
   can itself back up into a city's drains — this is not modeled here.
5. **Partial-pipe-flow simplification.** `capacity.py`'s
   `circular_pipe_capacity_m3s` uses full-pipe Manning's flow scaled by an
   optional `fill_ratio`, not a true partial-flow geometry (circular
   segment area/perimeter) solve.
6. **Effective-capacity priors are engineering assumptions, not
   measurements** (`capacity.DEFAULT_EFFECTIVE_FACTOR`): 0.70 for piped
   sewer, 0.55 for open nala, etc. These are exactly what inverse
   calibration is meant to correct, but the starting values themselves
   are not derived from any inspection/condition-survey data (none was
   available).
7. **Calibration parameter granularity.** One capacity multiplier per
   `edge_kind` (4 parameters total), not per-edge, to avoid overfitting a
   demo-sized network to a single historical event — see
   `calibration/inverse_calibration.py` module docstring. At the extreme
   192mm/6h event, classification metrics can be insensitive to these
   factors within their plausible range even though the factors
   themselves do move during optimization (see `docs/experiment_results.md`).
8. **15/30-minute nowcast points are linearly interpolated** between
   hourly simulation states, not independently solved at sub-hourly
   resolution (`hydrology/nowcast.py`).
9. **Risk thresholds are configurable, not validated**
   (`core/schemas.RiskThresholds`, `validated=False` flag carried through
   to every API response) — spec section 8 explicitly says not to claim
   validated thresholds.
10. **Confidence-vs-lead-time decay** in the nowcast (`nowcast._confidence_for_horizon`)
    is an explicit, simple decay curve, not derived from any measured
    forecast-skill-vs-lead-time study.

## CCTV module limitations
- The flood detector (`cctv/flood_detection.py`) is a hand-written
  heuristic (brightness / saturation / horizontal-edge-banding), not a
  trained model — no labeled real flood-CCTV dataset was available to
  train or validate one. It works reliably on this repo's own synthetic
  demo frames (see `tests/test_pipeline.py`) but has **not** been
  validated against real footage.
- Estimated depth from CCTV is a **rough proxy** (a function of the
  detector's own confidence score, scaled into a 5–40cm range), not a
  measured depth — there's no reference object/gauge visible in frame to
  calibrate against. A real system needs either a fixed reference object
  in each camera's field of view or a trained depth-regression model.
- Only 3 demo cameras exist, hand-placed; a real deployment would use
  actual GHMC/traffic-police camera locations and feeds.

## Routing/alerts limitations
- The road graph is built by geometrically intersecting the (synthetic)
  road LineStrings — a real OSM/TGRAC roads layer already has topology
  and wouldn't need this intersection-splitting step, or would need a
  more robust version of it (this one doesn't handle near-miss
  non-intersecting overlaps, only exact geometric intersections).
- Risk-based routing penalties (`routing/flood_aware_routing.py`'s
  `RISK_PENALTY_MULTIPLIER` / `PROFILE_RISK_TOLERANCE`) are policy
  choices, not derived from any validated emergency-response
  operations-research study.
- Alerts are English + Telugu template strings only; no real SMS/IVR
  provider is wired up (by design — see `alerts/alert_service.py`, no
  hardcoded API keys, nothing dispatches without real credentials in the
  environment). Cell Broadcast is explicitly out of scope (spec section 10).

## Event-based validation limitation
Only one real-world-referenced historical event (13 Oct 2020) is used.
True multi-event holdout validation (spec section 6: "so the model is not
simply overfitted to one storm") would need multiple independently
observed real events; `calibration/event_validation.py` demonstrates the
*mechanism* using a second synthetic storm, explicitly labeled as such —
see that module's docstring.

## Next steps to remove these limitations (roughly in priority order)
1. Run `scripts/fetch_real_data.sh` from a machine with real internet
   access to pull actual TGRAC layers, IMD rainfall, and a real DEM;
   replace the `Catchment` class's synthetic generator calls with these.
2. Source a real historical flood-extent dataset (GHMC damage
   assessment, OpenCity, or manually geocoded/verified news reports) with
   at least 2-3 independent events for genuine holdout validation.
3. Replace cell→nearest-node runoff assignment with full DEM
   flow-accumulation-path routing to the network.
4. Replace the CCTV heuristic with a trained segmentation/detection model
   once labeled real flood-CCTV imagery is available, and add a real
   reference-object-based (or stereo/lidar) depth estimate.
5. Swap the mass-balance hydraulic core for EPA SWMM (or pyswmm) for
   real hydraulic accuracy once a real network + real historical
   calibration data exist to justify the added complexity.
6. Validate risk thresholds and routing risk-tolerance policies against
   either historical outcomes or domain-expert (GHMC/HMWSSB/disaster
   management) review before using them operationally.
