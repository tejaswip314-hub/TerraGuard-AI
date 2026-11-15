#!/usr/bin/env python3
"""
Stage C: Assemble India-wide RF training dataset from the verified national grid
and GSI national landslide inventory, then train the RF model with honest
spatial cross-validation.

Labels   : GSI national landslide inventory (Points, EPSG:4326).
Coverage : 353 audited SRTM GL1 tiles reprojected onto the ~1 km national grid.
Features : elevation, slope, aspect_sin, aspect_cos, hillshade.
Protocol : GroupKFold by 1-degree tile; OOF metrics (ROC-AUC, PR-AUC, F1);
           frozen threshold from pooled OOF F1 (argmax). RF v3-style.
"""
import csv
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import rasterio
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GroupKFold
from sklearn.metrics import roc_auc_score, average_precision_score, precision_recall_fscore_support, confusion_matrix
import joblib

ROOT = Path(r"D:\TerraGuardAI")
GRID_DIR = ROOT / "data" / "processed" / "terrain_india"
GSI_PATH = ROOT / "data" / "raw" / "landslide_inventory" / "external" / "gsi_extracted" / "GSI_Landslide_Inventory.geojsonl"
OUT_CSV = ROOT / "data" / "processed" / "india_rf_training_samples.csv"
MODEL_PATH = ROOT / "models" / "rf_india_susceptibility_v1.joblib"
META_PATH = ROOT / "models" / "rf_india_susceptibility_v1_metadata.json"

RES = 0.01
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = 68.0, 6.0, 98.0, 37.0
WIDTH = int(round((LON_MAX - LON_MIN) / RES))
HEIGHT = int(round((LAT_MAX - LAT_MIN) / RES))
NODATA = -9999.0

# ── Load grid rasters ───────────────────────────────────────────────────────
def load(name):
    with rasterio.open(GRID_DIR / name) as ds:
        arr = ds.read(1)
    return arr

print("Loading grid rasters…")
z = load("india_dem_4326_1km.tif")
slope = load("india_slope_4326_1km.tif")
aspect = load("india_aspect_4326_1km.tif")
hill = load("india_hillshade_4326_1km.tif")

covered = z != NODATA
print(f"covered cells: {int(np.count_nonzero(covered)):,}")

# ── Load GSI landslide points (unique coordinates) ─────────────────────────
import json as _json
seen = set()
points = []
with open(GSI_PATH, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if not line.strip():
            continue
        try:
            r = _json.loads(line, strict=False)
        except Exception:
            continue
        p = r.get("properties", {})
        try:
            lon = float(p.get("LONGITUDE"))
            lat = float(p.get("LATITUDE"))
        except Exception:
            continue
        if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
            continue
        key = (round(lon, 5), round(lat, 5))
        if key in seen:
            continue
        seen.add(key)
        points.append((lon, lat))
print(f"unique GSI points in grid: {len(points):,}")

# Map points to grid cells
def cell(lon, lat):
    col = int((lon - LON_MIN) / RES)
    row = int((LAT_MAX - lat) / RES)
    if 0 <= col < WIDTH and 0 <= row < HEIGHT:
        return row, col
    return None, None

landslide_cells = {}
for lon, lat in points:
    row, col = cell(lon, lat)
    if row is None:
        continue
    if covered[row, col]:
        landslide_cells[(row, col)] = (lon, lat)
print(f"landslide cells with valid terrain: {len(landslide_cells):,}")

# ── Sample background cells ──────────────────────────────────────────────────
# Exclude landslide cells and 1-cell buffer around them
exclusion = np.zeros_like(covered)
for (row, col), _ in landslide_cells.items():
    r0, r1 = max(0, row - 1), min(HEIGHT, row + 2)
    c0, c1 = max(0, col - 1), min(WIDTH, col + 2)
    exclusion[r0:r1, c0:c1] = True
background_mask = covered & ~exclusion
n_bg_available = int(np.count_nonzero(background_mask))
print(f"available background cells (excl. landslide+buffer): {n_bg_available:,}")

BG = min(len(landslide_cells), n_bg_available)  # balanced classes
print(f"sampling {BG:,} background cells")

random.seed(42)
bg_cells = []
# Reservoir-efficient: rejection sampling over flat indices
indices = np.flatnonzero(background_mask.ravel())
random.shuffle(indices)
bg_cells = [int(i) for i in indices[:BG]]

def cell_of(flat_idx):
    return divmod(flat_idx, WIDTH)

# ── Build feature arrays ─────────────────────────────────────────────────────
rows = []
# landslides
for (row, col), (lon, lat) in landslide_cells.items():
    rows.append((lon, lat, float(z[row, col]), float(slope[row, col]),
                 float(aspect[row, col]), float(hill[row, col]), 1, f"{int(math.floor(lon))}_{int(math.floor(lat))}"))
# backgrounds
for flat in bg_cells:
    row, col = cell_of(flat)
    lon = LON_MIN + (col + 0.5) * RES
    lat = LAT_MAX - (row + 0.5) * RES
    rows.append((lon, lat, float(z[row, col]), float(slope[row, col]),
                 float(aspect[row, col]), float(hill[row, col]), 0, f"{int(math.floor(lon))}_{int(math.floor(lat))}"))

# Keep only cells with full feature coverage (no NODATA slope/hillshade —
# boundary cells with missing neighbors would corrupt training)
rows = [r for r in rows if r[3] != NODATA and r[5] != NODATA]

import pandas as pd
df = pd.DataFrame(rows, columns=["lon", "lat", "elevation", "slope", "aspect", "hillshade", "label", "tile_group"])
df["aspect_sin"] = np.where(df["aspect"] == NODATA, 0.0, np.sin(np.radians(df["aspect"])))
df["aspect_cos"] = np.where(df["aspect"] == NODATA, 1.0, np.cos(np.radians(df["aspect"])))
FEATURES = ["elevation", "slope", "aspect_sin", "aspect_cos", "hillshade"]
print(df["label"].value_counts())
print("feature ranges:")
print(df[FEATURES].describe())
df.to_csv(OUT_CSV, index=False)
print(f"training CSV: {OUT_CSV} ({len(df):,} rows)")

# ── Train with GroupKFold by 1-degree tile ───────────────────────────────────
X = df[FEATURES].to_numpy(dtype=np.float64)
y = df["label"].to_numpy(dtype=np.int64)
groups = df["tile_group"].to_numpy()

print("Training RandomForest (n_estimators=200, balanced)…")
t0 = time.time()
model = RandomForestClassifier(
    n_estimators=200,
    class_weight="balanced",
    random_state=42,
    n_jobs=-1,
    max_depth=None,
    min_samples_split=5,
    min_samples_leaf=2,
)
model.fit(X, y)
print(f"trained in {time.time()-t0:.1f}s")

# OOF evaluation by tile group
gkf = GroupKFold(n_splits=5)
oof_proba = np.zeros(len(y), dtype=np.float64)
for fold, (tr, te) in enumerate(gkf.split(X, y, groups), start=1):
    m = RandomForestClassifier(
        n_estimators=200, class_weight="balanced", random_state=42 + fold, n_jobs=-1,
        max_depth=None, min_samples_split=5, min_samples_leaf=2,
    )
    m.fit(X[tr], y[tr])
    oof_proba[te] = m.predict_proba(X[te])[:, 1]
    print(f"  fold {fold}/5 done ({time.time()-t0:.1f}s)")

roc = roc_auc_score(y, oof_proba)
pr = average_precision_score(y, oof_proba)
# Frozen threshold = argmax F1 over OOF predictions
thresholds = np.unique(np.round(oof_proba, 3))
best_f1, best_t = 0.0, 0.5
for t in thresholds:
    pred = (oof_proba >= t).astype(int)
    if pred.sum() == 0:
        continue
    p, r, f1, _ = precision_recall_fscore_support(y, pred, average="binary", zero_division=0)
    if f1 > best_f1:
        best_f1, best_t = f1, t
pred_frozen = (oof_proba >= best_t).astype(int)
p, r, f1, _ = precision_recall_fscore_support(y, pred_frozen, average="binary", zero_division=0)
cm = confusion_matrix(y, pred_frozen)

print()
print("OOF metrics:")
print(f"  ROC-AUC : {roc:.4f}")
print(f"  PR-AUC  : {pr:.4f}")
print(f"  Frozen threshold (argmax OOF F1): {best_t}")
print(f"  At frozen threshold — P={p:.4f} R={r:.4f} F1={f1:.4f}")
print(f"  Confusion matrix (OOF, frozen t): TN={cm[0,0]} FP={cm[0,1]} FN={cm[1,0]} TP={cm[1,1]}")

joblib.dump(model, MODEL_PATH, compress=3)

meta = {
    "model": "rf_india_susceptibility_v1",
    "model_file": "models/rf_india_susceptibility_v1.joblib",
    "training_date": time.strftime("%Y-%m-%d %H:%M:%S"),
    "features": FEATURES,
    "training_samples": int((y == 1).sum()),
    "background_samples": int((y == 0).sum()),
    "total_samples": int(len(y)),
    "unique_gsi_points_seen": int(len(points)),
    "landslide_cells_covered": int(len(landslide_cells)),
    "protocol": "GroupKFold(n_splits=5) by 1-degree tile; OOF metrics; threshold from argmax OOF F1",
    "metrics_oof": {
        "roc_auc": float(roc),
        "pr_auc": float(pr),
        "frozen_threshold": float(best_t),
        "precision": float(p),
        "recall": float(r),
        "f1": float(f1),
        "confusion_matrix": {"TN": int(cm[0,0]), "FP": int(cm[0,1]), "FN": int(cm[1,0]), "TP": int(cm[1,1])},
    },
    "data_sources": [
        "GSI national landslide inventory (external, 2016-era composite) — 30,842 records, 28,362 unique points, all in India",
        "SRTM GL1 (NASA/USGS, 1 arc-second) — 353 audited tiles (EPSG:4326); N09E077 corrupt and excluded",
        "National grid: EPSG:4326, 0.01 deg (~1 km), bbox lon 68-98, lat 6-37",
    ],
    "limitations": [
        "Experimental, uncalibrated susceptibility screening — NOT a calibrated probability",
        "Resolution ~1 km; not valid at sub-km scales",
        "Labels: GSI inventory mixes earthquake-, rainfall-, and other-triggered events; trigger-agnostic point model",
        "Background sampled randomly from covered cells; landslide cells excluded with 1-cell buffer",
        "No geology/landcover/NDVI/proximity features (not available nationally in verified form)",
        "Coverage: ~38% of the national bbox; predictions outside covered cells are unavailable, never fabricated",
        "GroupKFold by 1-degree tile — spatial leakage minimized but not eliminated",
    ],
    "not_applicable": [
        "Chamoli pilot RF v3 remains a separate, finer-scale (~30 m) experimental model",
        "Rainfall is NOT a feature; Open-Meteo rainfall is a separate screening layer",
    ],
}
with open(META_PATH, "w") as f:
    json.dump(meta, f, indent=2)
print(f"model: {MODEL_PATH}")
print(f"metadata: {META_PATH}")
print("STAGE C COMPLETE")
