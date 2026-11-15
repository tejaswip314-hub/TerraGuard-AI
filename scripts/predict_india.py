#!/usr/bin/env python3
"""
Stage D: Predict RF v1 susceptibility for every covered grid cell,
write GeoTIFF + colored PNG overlay + Leaflet metadata JSON.
"""
import json
from pathlib import Path

import numpy as np
import rasterio
import joblib

from PIL import Image

ROOT = Path(r"D:\TerraGuardAI")
GRID_DIR = ROOT / "data" / "processed" / "terrain_india"
MODEL_PATH = ROOT / "models" / "rf_india_susceptibility_v1.joblib"
RES = 0.01
LON_MIN, LAT_MAX = 68.0, 37.0
NODATA = -9999.0
FROZEN_THRESHOLD = 0.402  # from Stage C metadata (argmax OOF F1)

print("Loading grid rasters and model…")
with rasterio.open(GRID_DIR / "india_dem_4326_1km.tif") as ds:
    z = ds.read(1)
    H, W = z.shape
with rasterio.open(GRID_DIR / "india_slope_4326_1km.tif") as ds:
    slope = ds.read(1)
with rasterio.open(GRID_DIR / "india_aspect_4326_1km.tif") as ds:
    aspect = ds.read(1)
with rasterio.open(GRID_DIR / "india_hillshade_4326_1km.tif") as ds:
    hill = ds.read(1)

model = joblib.load(MODEL_PATH)

# Identify predictable cells: covered dem AND valid slope/hillshade
valid = (z != NODATA) & (slope != NODATA) & (hill != NODATA)
print(f"predictable cells: {int(np.count_nonzero(valid)):,} of {H*W:,}")

prob_grid = np.full((H, W), NODATA, dtype=np.float32)

# Predict in row chunks to keep memory small
rows_idx = np.flatnonzero(valid.any(axis=1))
CHUNK = 128
for start in range(0, len(rows_idx), CHUNK):
    r0 = rows_idx[start]
    r1 = rows_idx[min(start + CHUNK, len(rows_idx)) - 1] + 1
    block = valid[r0:r1]
    if not block.any():
        continue
    asp = aspect[r0:r1]
    asp_sin = np.where(asp == NODATA, 0.0, np.sin(np.deg2rad(asp)))
    asp_cos = np.where(asp == NODATA, 1.0, np.cos(np.deg2rad(asp)))
    X = np.column_stack([
        z[r0:r1][block].astype(np.float64),
        slope[r0:r1][block].astype(np.float64),
        asp_sin[block].astype(np.float64),
        asp_cos[block].astype(np.float64),
        hill[r0:r1][block].astype(np.float64),
    ])
    prob_grid[r0:r1][block] = model.predict_proba(X)[:, 1].astype(np.float32)
    if (start // CHUNK) % 10 == 0:
        print(f"  rows {r0}-{r1} done")

# ── Write GeoTIFF ───────────────────────────────────────────────────────────
from rasterio.transform import from_origin
transform = from_origin(LON_MIN, LAT_MAX, RES, RES)
out_tif = GRID_DIR / "susceptibility_india_experimental.tif"
with rasterio.open(
    out_tif, "w", driver="GTiff", height=H, width=W, count=1,
    dtype="float32", crs="EPSG:4326", transform=transform, nodata=NODATA,
    compress="deflate", predictor=2,
) as ds:
    ds.write(prob_grid, 1)
print(f"written: {out_tif} ({out_tif.stat().st_size/1e6:.1f} MB)")

np.save(GRID_DIR / "india_susceptibility_grid.npy", prob_grid)

# ── Build colorized PNG overlay (green → yellow → orange → red) ──────────────
validp = (prob_grid != NODATA) & np.isfinite(prob_grid) & (prob_grid >= 0)
print(f"valid probability cells: {int(np.count_nonzero(validp)):,}")

def colorize(p):
    """Map 0..1 to RGB via green→yellow→orange→red."""
    p = np.clip(p, 0.0, 1.0)
    r = np.where(p < 0.5, (p * 2.0) * 255, 255.0)
    green = np.where(p < 0.5, 255.0 - (p * 2.0) * 80.0, 255.0 - ((p - 0.5) * 2.0) * 200.0)
    b = np.zeros_like(p)
    return r, green, b

r, g, b = colorize(np.where(validp, prob_grid, 0.0))
alpha = np.where(validp, (np.clip(prob_grid, 0, 1) * 0.65 + 0.25) * 255, 0).astype(np.uint8)

img = np.stack([
    np.where(validp, r, 0).astype(np.uint8),
    np.where(validp, g, 0).astype(np.uint8),
    np.where(validp, b, 0).astype(np.uint8),
    alpha,
], axis=-1)

out_png = GRID_DIR / "susceptibility_india_experimental_overlay.png"
Image.fromarray(img, mode="RGBA").save(out_png, "PNG", optimize=True)
print(f"written: {out_png} ({out_png.stat().st_size/1e6:.1f} MB)")

# ── Overlay metadata JSON ─────────────────────────────────────────────────────
valid_vals = prob_grid[validp]
stats = {
    "min": float(valid_vals.min()),
    "max": float(valid_vals.max()),
    "mean": float(valid_vals.mean()),
    "median": float(np.median(valid_vals)),
    "p90": float(np.percentile(valid_vals, 90)),
    "p99": float(np.percentile(valid_vals, 99)),
    "p_ge_frozen_threshold": float(np.mean(valid_vals >= FROZEN_THRESHOLD)),
    "n_cells": int(valid_vals.size),
}

overlay = {
    "image": "/susceptibility/india_overlay.png",
    "bounds": [[6.0, 68.0], [37.0, 98.0]],
    "crs": "EPSG:4326",
    "resolution_deg": RES,
    "mode": "continuous green(low)→yellow→orange→red(high); transparent where outside SRTM coverage",
    "model": "RF v1-national (rf_india_susceptibility_v1.joblib), EXPERIMENTAL",
    "threshold": FROZEN_THRESHOLD,
    "features": ["elevation", "slope", "aspect_sin", "aspect_cos", "hillshade"],
    "aoi": "Regions covered by 353 audited SRTM GL1 tiles (~1 km grid)",
    "nodata": "Cells without verified DEM coverage are transparent; NO predictions fabricated outside coverage",
    "disclaimer": [
        "EXPERIMENTAL nationwide susceptibility screening. NOT an operational landslide forecast.",
        "Trained on GSI national landslide inventory (trigger-agnostic points).",
        "No geology/landcover/hydrology features nationally; terrain-only model.",
        "Do not use for emergency decision-making or public safety signage.",
        "Rainfall is NOT a predictor; rainfall monitoring is a separate Open-Meteo layer.",
    ],
    "probability_stats": stats,
}
out_json = GRID_DIR / "susceptibility_india_experimental_overlay.json"
with open(out_json, "w") as f:
    json.dump(overlay, f, indent=2)
print(f"written: {out_json}")
print(json.dumps(stats, indent=2))
print("STAGE D COMPLETE")
