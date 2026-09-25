# Historical event used for calibration: 13 October 2020 Hyderabad floods

**What is real (public, verifiable fact):**
- A deep depression over the Bay of Bengal (BoB 02) made landfall on 13 Oct
  2020 and brought record rainfall to Hyderabad, causing severe urban
  flash flooding and overflow of the Musi River and several city lakes.
- IMD reported a citywide record 24-hour rainfall, with a GHMC-wide average
  around 192 mm and a peak station reading of 324.5 mm — Hyderabad's
  highest recorded daily rainfall, surpassing the prior record of 241.5 mm
  (24 Aug 2000).
- At least 23 areas received over 205 mm ("very heavy") in 24 hours; over
  100 fatalities were reported across Telangana/Andhra Pradesh from this
  event; roughly 20,000+ houses were reported inundated across 100+
  localities.
- GHMC logged 236+ waterlogging/drainage-overflow complaints on its
  helpline the same morning.

Sources: IMD Mausam journal study of the event (Singh et al.), contemporary
reporting (The News Minute, Gulf News, Team-BHP compiling IMD figures),
Wikipedia's "2020 Hyderabad floods" summary.

**What is NOT real in this repo:** the specific set of flooded *nodes* used
as ground truth for the inverse-calibration metrics in
`backend/app/calibration/`. No geotagged, node-level flood-extent dataset
for this event was accessible in the sandbox this prototype was built in.
The calibration module therefore uses `generate_historical_inundation()`
(synthetic polygons placed in the physically low-lying SE part of the demo
catchment, consistent with where flooding concentrates in the model) as a
stand-in ground truth, clearly tagged `source="synthetic-demo"`.

**To make this fully real:** replace
`synthetic_catchment.generate_historical_inundation()` with a loader for
GHMC's post-event damage assessment, the OpenCity Hyderabad flooding
layer, or manually geocoded news reports, using the same
`INUNDATION_SCHEMA` contract in `core/schemas.py` — the calibration code
does not need to change.
