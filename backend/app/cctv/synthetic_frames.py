"""
Generates clearly-labeled SYNTHETIC demo CCTV frames (a dry road scene and
a waterlogged road scene) so the CCTV pipeline has something to run on
end-to-end. Real CCTV feeds (GHMC/traffic police cameras) were not
accessible in this environment — see data_inventory.md item 11.

These are simple procedural images (not photorealistic), intentionally, so
nobody mistakes them for real footage: each is stamped with a visible
"SYNTHETIC DEMO FRAME" watermark.
"""
from __future__ import annotations
import numpy as np
import cv2
import os


def _make_frame(waterlogged: bool, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h, w = 240, 320
    img = np.zeros((h, w, 3), dtype=np.uint8)

    # sky
    img[0:60, :] = (200, 170, 140)  # BGR light blue-ish
    # road / buildings backdrop
    img[60:150, :] = (90, 90, 95)
    # road surface
    road_top = 150
    if waterlogged:
        # dark, low-texture, strongly horizontally-banded water surface
        # (ripple/reflection bands) -- darker and flatter than dry asphalt,
        # with pronounced horizontal structure, tuned to be the clearest
        # discriminative signal for the heuristic detector below
        base = np.array([38, 42, 40], dtype=np.int16)  # dark murky water BGR
        road = np.tile(base, (h - road_top, w, 1)).astype(np.uint8)
        for i in range(0, h - road_top, 4):
            band_shade = int(18 * np.sin(i / 5.0)) + int(rng.integers(-4, 4))
            road[i:i + 2, :] = np.clip(base + band_shade, 0, 255).astype(np.uint8)
        img[road_top:, :] = road
        # a few faint reflection streaks (also horizontal)
        for _ in range(4):
            y = rng.integers(road_top + 5, h - 5)
            cv2.line(img, (0, y), (w, y), (60, 65, 62), 1, lineType=cv2.LINE_AA)
    else:
        base = np.array([95, 95, 97], dtype=np.int16)  # dry asphalt, lighter & neutral grey
        road = np.tile(base, (h - road_top, w, 1)).astype(np.uint8)
        # isotropic speckle texture (asphalt grain) -- NOT horizontally banded
        speckle = rng.integers(-10, 10, size=(h - road_top, w))
        road = np.clip(road.astype(np.int16) + speckle[..., None], 0, 255).astype(np.uint8)
        img[road_top:, :] = road
        # lane marking
        cv2.line(img, (w // 2, road_top), (w // 2, h), (200, 200, 200), 2, lineType=cv2.LINE_AA)

    cv2.putText(img, "SYNTHETIC DEMO FRAME", (6, 14), cv2.FONT_HERSHEY_SIMPLEX,
                0.38, (0, 0, 255), 1, cv2.LINE_AA)
    label = "waterlogged" if waterlogged else "dry"
    cv2.putText(img, f"cam_demo | {label}", (6, h - 6), cv2.FONT_HERSHEY_SIMPLEX,
                0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def generate_demo_frames(out_dir: str) -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    scenarios = [
        ("SYNTHETIC_cam01_dry.jpg", False, 0, 17.3705, 78.4680),
        ("SYNTHETIC_cam02_waterlogged.jpg", True, 1, 17.3695, 78.4715),
        ("SYNTHETIC_cam03_waterlogged_severe.jpg", True, 2, 17.3680, 78.4760),
    ]
    for fname, waterlogged, seed, lat, lon in scenarios:
        img = _make_frame(waterlogged, seed=seed)
        path = os.path.join(out_dir, fname)
        cv2.imwrite(path, img)
        frames.append({"camera_id": fname.split("_")[1], "lat": lat, "lon": lon,
                        "frame_path": path, "source": "synthetic-demo"})
    return frames
