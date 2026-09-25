"""
CCTV observation module (spec section 7).

Baseline detector: a lightweight, explainable heuristic (NOT a trained
model — none was available/trainable in this environment) operating on the
lower half of the frame ("road region"):
  - Water/flood surfaces tend to be darker, less saturated, and more
    horizontally-banded/reflective than dry asphalt or pavement texture.
  - We compute: (a) mean brightness, (b) horizontal-edge-to-total-edge
    ratio (water reflections + wave-lines create strong horizontal edges;
    dry road texture is more isotropic/noisy), (c) color muddiness (low
    saturation, brownish-grey hue band).
  - A weighted score combines these into a flood probability in [0,1].

This is a genuine, functioning baseline (runs on real or synthetic
frames), but it is explicitly NOT validated against a labeled real-world
flood-CCTV dataset in this repo — see docs/limitations.md. The interface
(`detect_flood(frame) -> FloodObservation`) is written so a real trained
segmentation model (e.g. a fine-tuned semantic segmentation net) can be
swapped in without touching the assimilation code below.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import cv2


@dataclass
class FloodObservation:
    camera_id: str
    lat: float
    lon: float
    timestamp: str
    flood_detected: bool
    estimated_depth_m: float | None
    confidence: float
    method: str = "heuristic-cv-v0"


def detect_flood(frame_bgr: np.ndarray, camera_id: str, lat: float, lon: float,
                   timestamp: str) -> FloodObservation:
    h, w, _ = frame_bgr.shape
    road_region = frame_bgr[int(h * 0.55):, :]  # lower ~45% = road/ground

    blurred = cv2.GaussianBlur(road_region, (5, 5), 0)
    gray = cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(road_region, cv2.COLOR_BGR2HSV)

    mean_brightness = float(gray.mean())
    mean_saturation = float(hsv[:, :, 1].mean())

    sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    horiz_energy = float(np.abs(sobel_y).mean())
    vert_energy = float(np.abs(sobel_x).mean())
    horiz_ratio = horiz_energy / (horiz_energy + vert_energy + 1e-6)

    # heuristic scoring, each term normalized to roughly [0,1]
    darkness_score = np.clip((90 - mean_brightness) / 60.0, 0, 1)      # darker -> more water-like
    low_sat_score = np.clip((80 - mean_saturation) / 80.0, 0, 1)        # muddy/low-saturation
    band_score = np.clip((horiz_ratio - 0.5) * 2, 0, 1)                  # horizontal banding

    flood_score = float(0.4 * darkness_score + 0.25 * low_sat_score + 0.35 * band_score)
    flood_score = float(np.clip(flood_score, 0, 1))
    detected = flood_score >= 0.5

    # crude depth proxy: only meaningful once detected; scales 5cm-40cm
    # over the score range [0.5, 1.0]. Explicitly a rough proxy, not a
    # measured depth (no reference object/gauge in frame).
    depth = None
    if detected:
        depth = round(float(0.05 + (flood_score - 0.5) / 0.5 * 0.35), 3)

    confidence = float(np.clip(abs(flood_score - 0.5) * 2, 0.15, 0.9))  # more decisive score -> higher confidence

    return FloodObservation(
        camera_id=camera_id, lat=lat, lon=lon, timestamp=timestamp,
        flood_detected=detected, estimated_depth_m=depth, confidence=round(confidence, 2),
    )


def assimilate(predicted_depth_m: float, observation: FloodObservation,
                 assimilation_weight: float | None = None) -> dict:
    """State assimilation (spec section 7): correct the *current local
    flood state* using the CCTV observation. This is a single-step,
    confidence-weighted blend (a simplified Kalman-style update with a
    fixed "measurement noise" derived from the CV model's own confidence),
    explicitly NOT a permanent change to infrastructure capacity —
    capacity recalibration is the separate, slower `calibration` module.

        corrected = w*observed + (1-w)*predicted,  w = observation.confidence
                    (unless assimilation_weight is explicitly overridden)
    """
    if observation.estimated_depth_m is None:
        return {"corrected_depth_m": predicted_depth_m, "weight_used": 0.0,
                "reason": "no positive detection; prediction unchanged"}
    w = assimilation_weight if assimilation_weight is not None else observation.confidence
    corrected = w * observation.estimated_depth_m + (1 - w) * predicted_depth_m
    return {
        "corrected_depth_m": round(float(corrected), 3),
        "predicted_depth_m": round(float(predicted_depth_m), 3),
        "observed_depth_m": observation.estimated_depth_m,
        "weight_used": round(float(w), 2),
        "residual_m": round(float(observation.estimated_depth_m - predicted_depth_m), 3),
    }
