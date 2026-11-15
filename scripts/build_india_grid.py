#!/usr/bin/env python3
"""
Stage A+B: Build the India-wide ~1 km DEM mosaic and terrain derivatives.

Reads the 353 verified SRTM GL1 tiles (EPSG:4326, 1 arc-second), reprojects them
into a national grid (0.01 deg ~ 1 km), and computes slope / aspect / hillshade
directly on that grid. NoData handling is explicit; nothing is fabricated.
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling

ROOT = Path(r"D:\TerraGuardAI")
TILES_DIR = ROOT / "data" / "raw" / "dem" / "india" / "srtmgl1"
AUDIT_CSV = ROOT / "data" / "processed" / "dem" / "india" / "srtmgl1_audit" / "srtmgl1_tile_audit.csv"
OUT_DIR = ROOT / "data" / "processed" / "terrain_india"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# National grid definition (EPSG:4326, degrees) — covers verified tile extent.
RES = 0.01  # ~1.1 km
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = 68.0, 6.0, 98.0, 37.0
WIDTH = int(round((LON_MAX - LON_MIN) / RES))
HEIGHT = int(round((LAT_MAX - LAT_MIN) / RES))
NODATA = -9999.0

print(f"National grid: {WIDTH} x {HEIGHT} = {WIDTH*HEIGHT:,} cells at {RES} deg")
print(f"Extent: lon {LON_MIN}-{LON_MAX}, lat {LAT_MIN}-{LAT_MAX}")

# ── Load verified tiles from the audit ──────────────────────────────────────
import csv
verified = []
with open(AUDIT_CSV, newline="", encoding="utf-8", errors="replace") as f:
    for row in csv.DictReader(f):
        tile = row.get("tile", "")
        status = row.get("manifest_status", "")
        try:
            pct = float(row.get("valid_pixel_pct") or 0)
        except ValueError:
            pct = 0.0
        if ("valid" in status or "existing" in status) and pct > 0:
            verified.append(tile)
print(f"verified tiles (audit): {len(verified)}")

dst = np.full((HEIGHT, WIDTH), NODATA, dtype=np.float32)
transform = from_origin(LON_MIN, LAT_MAX, RES, RES)
crs = CRS.from_epsg(4326)

import math
start = time.time()
skipped = []
for i, tile in enumerate(sorted(verified), start=1):
    path = TILES_DIR / tile
    try:
        with rasterio.open(path) as src:
            if src.crs != crs:
                print(f"  SKIP {tile}: unexpected CRS {src.crs}")
                skipped.append(tile)
                continue
            left, bottom, right, top = src.bounds
            col0 = max(0, int((left - LON_MIN) / RES))
            col1 = min(WIDTH, int(math.ceil((right - LON_MIN) / RES)))
            row0 = max(0, int((LAT_MAX - top) / RES))
            row1 = min(HEIGHT, int(math.ceil((LAT_MAX - bottom) / RES)))
            if col1 <= col0 or row1 <= row0:
                print(f"  SKIP {tile}: outside grid window")
                skipped.append(tile)
                continue
            win = dst[row0:row1, col0:col1]
            wtransform = from_origin(LON_MIN + col0 * RES, LAT_MAX - row0 * RES, RES, RES)
            src_arr = src.read(1).astype(np.float32)
            src_arr[src_arr == src.nodata] = NODATA
            valid = np.count_nonzero(src_arr != NODATA)
            if valid < 1000:
                print(f"  SKIP {tile}: too few valid pixels ({valid})")
                skipped.append(tile)
                continue
            reproject(
                source=src_arr,
                destination=win,
                src_transform=src.transform,
                src_crs=crs,
                src_nodata=NODATA,
                dst_transform=wtransform,
                dst_crs=crs,
                dst_nodata=NODATA,
                dst_shape=win.shape,
                resampling=Resampling.average,
            )
    except Exception as e:
        print(f"  SKIP {tile}: {type(e).__name__}: {e}")
        skipped.append(tile)
        continue
    if i % 50 == 0:
        print(f"  [{i}/{len(verified)}] {time.time()-start:.1f}s")

print(f"reproject done in {time.time()-start:.1f}s; skipped: {skipped}")

# ── Save DEM mosaic ─────────────────────────────────────────────────────────
dem_path = OUT_DIR / "india_dem_4326_1km.tif"
with rasterio.open(
    dem_path, "w", driver="GTiff", height=HEIGHT, width=WIDTH, count=1,
    dtype="float32", crs=crs, transform=transform, nodata=NODATA,
) as ds:
    ds.write(dst, 1)
valid_cells = int(np.count_nonzero(dst != NODATA))
print(f"DEM mosaic: {dem_path}")
print(f"covered cells: {valid_cells:,} of {WIDTH*HEIGHT:,} ({100*valid_cells/(WIDTH*HEIGHT):.1f}%)")
print(f"elev min/max: {dst[dst != NODATA].min():.1f} / {dst[dst != NODATA].max():.1f} m")

# ── Stage B: derivatives on the DEM grid ────────────────────────────────────
# Pixel size in meters varies by latitude; compute row-wise x sizes.
lats = LAT_MAX - (np.arange(HEIGHT, dtype=np.float32) + 0.5) * RES
cos_lat = np.cos(np.deg2rad(lats))
MX = 111320.0      # m per degree longitude at equator
MY = 110540.0      # m per degree latitude
px_m_x = MX * cos_lat * RES          # per-row x pixel size (m)
py_m = MY * RES                      # y pixel size (m) constant

z = dst.astype(np.float64)
z_masked = np.where(z == NODATA, np.nan, z)

dy, dx = np.gradient(z_masked)  # central differences; NaN where no neighbors
# scale by meters per pixel (row-wise x factor)
slope_rad = np.arctan(np.sqrt((dx / px_m_x[:, None]) ** 2 + (dy / py_m) ** 2))
slope_deg = np.degrees(slope_rad)

# Aspect (azimuth of downslope direction, degrees, 0=N, 90=E) — Horn convention:
aspect = np.degrees(np.arctan2(dx, -dy))
aspect = np.where(aspect < 0, aspect + 360.0, aspect)

# Hillshade (azimuth=315, altitude=45)
azi = np.radians(315.0)
alt = np.radians(45.0)
slope_rad2 = np.radians(slope_deg)
asp_rad = np.radians(aspect)
hillshade = (
    np.cos(alt) * np.cos(slope_rad2)
    + np.sin(alt) * np.sin(slope_rad2) * np.cos(azi - asp_rad)
) * 255.0

slope_out = np.where(np.isnan(slope_deg), NODATA, slope_deg).astype(np.float32)
aspect_out = np.where(np.isnan(aspect), NODATA, aspect).astype(np.float32)
hill_out = np.where(np.isnan(hillshade), NODATA, hillshade).astype(np.float32)

def write(path, arr):
    with rasterio.open(
        path, "w", driver="GTiff", height=HEIGHT, width=WIDTH, count=1,
        dtype="float32", crs=crs, transform=transform, nodata=NODATA,
    ) as ds:
        ds.write(arr, 1)
    print(f"written: {path}")

write(OUT_DIR / "india_slope_4326_1km.tif", slope_out)
write(OUT_DIR / "india_aspect_4326_1km.tif", aspect_out)
write(OUT_DIR / "india_hillshade_4326_1km.tif", hill_out)

stats = {
    "grid_resolution_deg": RES,
    "grid_cells": int(WIDTH * HEIGHT),
    "covered_cells": valid_cells,
    "coverage_fraction": round(valid_cells / (WIDTH * HEIGHT), 4),
    "tiles_processed": len(verified),
    "tiles_skipped": len(skipped),
    "skipped_tiles": skipped,
    "dem_min_m": float(np.nanmin(z_masked)),
    "dem_max_m": float(np.nanmax(z_masked)),
}
with open(OUT_DIR / "india_grid_stats.json", "w") as f:
    json.dump(stats, f, indent=2)
print(json.dumps(stats, indent=2))
print("STAGE A+B COMPLETE")
