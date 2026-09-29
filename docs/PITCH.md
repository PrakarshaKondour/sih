# HYDROLOOP — Pitch Script

Urban Flood Nowcasting System · SIH PS 26085
Target length: ~6 minutes spoken + demo. Time markers are guides, not hard cuts.

---

## 0. One-line hook (0:00–0:20)

> "When a monsoon cell parks over Hyderabad, the city finds out where the water
> is going only after cars are already floating in it. HYDROLOOP turns rainfall
> into a 0-to-3-hour street-level flood forecast — and then routes ambulances
> around the flooded roads before they get there."

Pause. Let the "before" land.

---

## 1. The problem (0:20–1:00)

- Indian cities get flash urban flooding, not river flooding — it's driven by
  intense rainfall hitting drains that can't cope, in specific low-lying pockets.
- Existing warnings are either city-wide ("heavy rain expected") or arrive
  after the fact. Neither tells a driver, a 108 ambulance, or a control room
  *which street* floods and *when*.
- The 13 October 2020 Hyderabad event — 192 mm in 6 hours — is our reference
  storm. Roads became impassable in minutes; emergency vehicles were routed
  into water because nobody had a live road-level picture.

The gap: **a physically-grounded, street-resolution, short-horizon (nowcast)
flood picture that feeds directly into navigation and alerts.**

---

## 2. What HYDROLOOP does — the loop (1:00–2:15)

One sentence per stage. This is the whole pipeline:

> "Rainfall → runoff → where water flows → what the drains can carry →
> a flood forecast → camera check → flood-aware routing → a bilingual alert."

Expanded, if asked:

1. **Rainfall in.** Demo synthetic profile, or real IMERG/IMD files when supplied.
2. **SCS-CN runoff.** Standard hydrology curve-number method converts rainfall +
   land cover into how much water actually runs off vs. soaks in.
3. **DEM flow routing.** A digital elevation model decides where that runoff
   flows downhill and pools.
4. **Drainage graph + Manning's equation.** We model the drain network as a
   graph and compute each link's real carrying capacity. Where inflow beats
   capacity, water backs up — that's a flood node.
5. **Nowcast.** Backend produces flood depth and per-road risk at
   T+0/15/30/60/120/180 minutes. One source of truth.
6. **CCTV verification.** Sample camera detections are matched to the nearest
   road and can raise its risk — a real-world sanity check on the model.
7. **Flood-aware routing.** Compares the normal shortest route against a
   flood-avoiding route; CRITICAL roads are treated as impassable and returned
   as `blocked_road_ids`. Profiles for normal / ambulance / fire.
8. **Bilingual alert.** HIGH/CRITICAL forecasts fire English + Telugu alerts,
   with an optional webhook POST.

Key architectural point to say out loud:
> "Rainfall enters the physics **once**. The map, the routing, and the alerts
> all read the *same* backend forecast — the dashboard never invents a second
> flood state. One physics, one truth."

---

## 3. Live demo (2:15–4:00)

Do this on screen. Narrate as you click.

1. `docker compose up` → open http://localhost:5173.
2. Select **Demo rainfall**. Scenario loads automatically.
3. "Watch the timeline — T+0 to T+3h. As the storm develops, low-lying nodes
   near the trunk nala go orange, then red."
4. Point at a red road: "This road is CRITICAL at T+60. Now watch routing."
5. Set start + destination, pick **ambulance** profile, horizon T+60.
6. "Normal shortest route runs through this flooded road. The flood-aware route
   goes around it. The blocked road is highlighted separately."
7. Show the **Telugu + English alert** for the HIGH/CRITICAL threshold.
8. If cameras wired: "This CCTV detection on this road bumped its risk — the
   model and the camera agree."

Fallback if Docker is slow: hit the API directly —
`GET /api/scenario/run?rainfall_mode=demo` — and show the JSON forecast.

---

## 4. Why it's credible — the honesty slide (4:00–5:00)

This is your differentiator. Lead with it, don't hide it.

> "Most hackathon flood demos quietly fake the accuracy number. We refuse to.
> Every layer in this build is labeled real or synthetic."

- **Real:** a bounded OpenStreetMap road extract for the demo catchment
  (© OpenStreetMap, ODbL). Real TGRAC/DEM/IMERG can drop in via
  `scripts/fetch_real_data.sh`.
- **Synthetic (labeled):** rainfall default, drainage, DEM, land cover,
  historical flood labels, CCTV. DEM-derived drainage links are tagged
  **INFERRED**, not real nala surveys.
- We ran a real **A→D experiment ladder** against the 13 Oct 2020 profile and
  report the actual numbers — including the parts that *didn't* improve:

  | Experiment | F1 |
  |---|---|
  | A. Rainfall only | 0.255 |
  | B. + DEM (infinite capacity) | 0.240 ← dropped, and we explain why |
  | C. + drainage capacity | 0.276 |
  | D. + inverse calibration | 0.276 |

- "At a 192 mm record storm, calibration doesn't move the *classification* —
  because nearly everything floods regardless of the exact capacity factor.
  At milder rainfall (35 mm) the same calibration lifts F1 from 0.269 to 0.312.
  The mechanism works; it's correctly insensitive at catastrophe scale."

Say the line:
> "These are SYNTHETIC VALIDATION numbers. They prove the pipeline runs
> end-to-end and behaves physically — not that we've validated real forecast
> skill. We wrote that down so nobody has to guess."

That candor *is* the pitch — judges trust a team that marks its own limits.

---

## 5. Path to scale (5:00–5:40)

From one 60×60 demo catchment to Hyderabad:

1. Plug in real TGRAC roads + real DEM (scripts are ready).
2. Source 2–3 more verified historical events → real holdout validation
   (`event_validation.py` already waits for this).
3. Full DEM flow-path routing instead of nearest-node assignment.
4. PostGIS persistence (a `with-db` compose profile already exists) — stop
   rebuilding the network each restart, serve multiple tiles.
5. Tile + parallelize the hydrology for city coverage.
6. Trained CCTV model + real GHMC/traffic-police feeds.
7. Swap the mass-balance core for EPA SWMM once real data justifies it.

> "Nothing here is a dead end. Every simplification has a named upgrade path."

---

## 6. Close (5:40–6:00)

> "HYDROLOOP is a working loop today: rainfall goes in, a street-level 3-hour
> flood forecast comes out, and it routes emergency vehicles around the water
> and warns residents in two languages. It's built on real hydrology, it's
> honest about what's synthetic, and every shortcut has a clear road to
> production. Give us real feeds and more historical events — the machine to
> use them is already built."

Stop. Take questions.

---

## Q&A ammo (keep in back pocket)

- **"Is the accuracy real?"** No — synthetic ground truth, and we label it as
  such everywhere. The pipeline and physics are real; validation against
  verified real flood extents is the explicit next step.
- **"Why not just use SWMM?"** Overkill until we have a calibrated real
  network. Our mass-balance core proves the loop now; SWMM is step 7.
- **"What's genuinely novel?"** The closed loop — physics forecast feeding
  routing and bilingual alerts off one source of truth — plus radical honesty
  about data provenance.
- **"CCTV — trained model?"** No, an untrained heuristic that does local state
  correction at camera-adjacent nodes only. Deliberately not allowed to
  rewrite infrastructure capacity from one frame (spec §7).
- **"Runs offline?"** Yes — Docker Compose, backend on :8000, frontend on
  :5173, synthetic demo needs no external feeds.
