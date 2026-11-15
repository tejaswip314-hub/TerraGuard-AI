#!/usr/bin/env python3
"""
TerraGuardAI — Custom SimpleHTTPRequestHandler
Serves frontend from frontend/public/ and implements all API endpoints.
Uses only Python standard library (http.server). No Flask, React, Vite, JSX.
"""

import sys
import os
import http.server
import socketserver
import json
import urllib.parse
import urllib.request
from pathlib import Path

# ── MIME types ──────────────────────────────────────────────────────────────
import mimetypes
mimetypes.add_type('application/json', '.json')
mimetypes.add_type('application/geo+json', '.geojson')
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('image/png', '.png')

# ── Paths ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # D:\TerraGuardAI
FRONTEND_DIR = PROJECT_ROOT / "frontend" / "public"     # Static files root

_API_DIR = str(Path(__file__).resolve().parent)
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)   # allows 'import screening_utils'

# ── Helper functions ──────────────────────────────────────────────────────
def send_json(handler, data):
    """Send a JSON response with proper headers."""
    body = json.dumps(data, indent=2).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_error_json(handler, code, message):
    """Send a JSON error response."""
    send_json(handler, {"error": message, "status": code})


# ── Susceptibility raster (RF v3, experimental) ──────────────────────────────
# The precomputed RF v3 output rasters. The 4326 variant covers a 2-degree box
# around the AOI; point lookups are single-pixel windowed reads (fast).
SUS_4326 = PROJECT_ROOT / "data" / "processed" / "susceptibility_chamoli_experimental_4326.tif"
SUS_4326_BOUNDS = (78.0, 30.0, 80.0, 32.0)  # left, bottom, right, top (approx)
OVERLAY_PNG = PROJECT_ROOT / "data" / "processed" / "susceptibility_chamoli_experimental_overlay.png"
OVERLAY_JSON = PROJECT_ROOT / "data" / "processed" / "susceptibility_chamoli_experimental_overlay.json"

INDIA_RASTER = PROJECT_ROOT / "data" / "processed" / "terrain_india" / "susceptibility_india_experimental.tif"
INDIA_OVERLAY_PNG = PROJECT_ROOT / "data" / "processed" / "terrain_india" / "susceptibility_india_experimental_overlay.png"
INDIA_OVERLAY_JSON = PROJECT_ROOT / "data" / "processed" / "terrain_india" / "susceptibility_india_experimental_overlay.json"
INDIA_GRID_RES = 0.01  # ~1 km
INDIA_GRID_ORIGIN_LON = 68.0
INDIA_GRID_ORIGIN_LAT_TOP = 37.0  # top row north edge

# Audited SRTM coverage (353 of 354 files in the national audit passed integrity checks)
COVERAGE_CSV = PROJECT_ROOT / "data" / "processed" / "dem" / "india" / "srtmgl1_audit" / "srtmgl1_tile_audit.csv"
RF_MODEL_NAME = "rf_pixel_susceptibility_pipeline_v3.joblib"
FROZEN_THRESHOLD = 0.38
INDIA_MODEL_NAME = "rf_india_susceptibility_v1.joblib"
INDIA_FROZEN_THRESHOLD = 0.402


def rf_susceptibility_point(lat, lon):
    """Sample the precomputed RF v3 susceptibility raster at (lat, lon).

    Returns an honest result dict. Never fabricates a probability: anything
    outside the Chamoli AOI / raster nodata returns a clear status instead.
    """
    aoi = {"lat_min": 30.10, "lat_max": 30.52, "lon_min": 78.90, "lon_max": 79.45}
    in_aoi = (aoi["lat_min"] <= lat <= aoi["lat_max"]
              and aoi["lon_min"] <= lon <= aoi["lon_max"])
    if not in_aoi:
        return {
            "service": "TerraGuard AI API",
            "status": "out_of_coverage",
            "coordinates": {"latitude": lat, "longitude": lon},
            "susceptibility_probability": None,
            "frozen_threshold": FROZEN_THRESHOLD,
            "model": RF_MODEL_NAME,
        "model_kind": "chamoli",
            "reason": "Point is outside the Chamoli training AOI (1999 Mw 6.6 event). "
                      "RF v3 was not trained for this region; no prediction is fabricated.",
            "no_fabricated_predictions": True,
        }

    try:
        import rasterio
        from rasterio.windows import Window
        from rasterio.transform import rowcol
    except Exception as e:
        return {
            "service": "TerraGuard AI API",
            "status": "rf_unavailable",
            "coordinates": {"latitude": lat, "longitude": lon},
            "susceptibility_probability": None,
            "frozen_threshold": FROZEN_THRESHOLD,
            "error": f"rasterio unavailable: {e}",
            "no_fabricated_predictions": True,
        }

    if not SUS_4326.exists():
        return {
            "service": "TerraGuard AI API",
            "status": "rf_unavailable",
            "coordinates": {"latitude": lat, "longitude": lon},
            "susceptibility_probability": None,
            "frozen_threshold": FROZEN_THRESHOLD,
            "error": f"Susceptibility raster missing at {SUS_4326}",
            "no_fabricated_predictions": True,
        }

    try:
        with rasterio.open(str(SUS_4326)) as ds:
            row, col = rowcol(ds.transform, lon, lat)
            if row < 0 or row >= ds.height or col < 0 or col >= ds.width:
                return {
                    "service": "TerraGuard AI API",
                    "status": "out_of_coverage",
                    "coordinates": {"latitude": lat, "longitude": lon},
                    "susceptibility_probability": None,
                    "frozen_threshold": FROZEN_THRESHOLD,
                    "model": RF_MODEL_NAME,
        "model_kind": "chamoli",
                    "reason": "Point is outside the susceptibility raster grid.",
                    "no_fabricated_predictions": True,
                }
            # single-pixel windowed read — tiny, fast
            val = ds.read(1, window=Window(col, row, 1, 1))[0, 0]
            nodata = ds.nodata
            import numpy as np
            if nodata is not None and (val == nodata or not np.isfinite(val)):
                return {
                    "service": "TerraGuard AI API",
                    "status": "nodata",
                    "coordinates": {"latitude": lat, "longitude": lon},
                    "susceptibility_probability": None,
                    "frozen_threshold": FROZEN_THRESHOLD,
                    "model": RF_MODEL_NAME,
        "model_kind": "chamoli",
                    "reason": "Point location has no valid terrain-derived susceptibility (nodata/masked).",
                    "no_fabricated_predictions": True,
                }
            prob = float(val)
    except Exception as e:
        return {
            "service": "TerraGuard AI API",
            "status": "error",
            "coordinates": {"latitude": lat, "longitude": lon},
            "susceptibility_probability": None,
            "frozen_threshold": FROZEN_THRESHOLD,
            "error": f"RF raster read error: {str(e)[:200]}",
            "no_fabricated_predictions": True,
        }

    return {
        "service": "TerraGuard AI API",
        "status": "inference_complete",
        "coordinates": {"latitude": lat, "longitude": lon},
        "susceptibility_probability": prob,
        "frozen_threshold": FROZEN_THRESHOLD,
        "model": RF_MODEL_NAME,
        "model_kind": "chamoli",
        "model_note": "Precomputed RF v3 raster (frozen experimental output). Point lookup, not a live model call.",
        "overlay": {
            "png": "/susceptibility/overlay.png",
            "bounds": "/susceptibility/overlay.json",
        },
        "no_fabricated_predictions": True,
    }


def india_susceptibility_point(lat, lon):
    """Sample the precomputed India-wide RF susceptibility raster at (lat, lon).

    The raster is a float32 0.01-degree (~1 km) EPSG:4326 grid built from 353
    audited SRTM tiles. Cells outside audited coverage carry NODATA (-9999)
    and return an honest 'outside_coverage' status — never a fabricated value.
    """
    if not (68.0 <= lon <= 98.0 and 6.0 <= lat <= 37.0):
        return {
            "service": "TerraGuard AI API",
            "status": "outside_coverage",
            "coordinates": {"latitude": lat, "longitude": lon},
            "susceptibility_probability": None,
            "frozen_threshold": INDIA_FROZEN_THRESHOLD,
            "model": INDIA_MODEL_NAME,
        "model_kind": "india",
            "reason": "Point is outside the India RF coverage grid (lon 68-98, lat 6-37).",
            "no_fabricated_predictions": True,
        }
    try:
        import rasterio
        from rasterio.windows import Window
        from rasterio.transform import rowcol
        with rasterio.open(str(INDIA_RASTER)) as ds:
            row, col = rowcol(ds.transform, lon, lat)
            if row < 0 or row >= ds.height or col < 0 or col >= ds.width:
                value = -9999.0
            else:
                value = ds.read(1, window=Window(col, row, 1, 1))[0, 0]
            if value == -9999.0 or value != value:
                return {
                    "service": "TerraGuard AI API",
                    "status": "outside_coverage",
                    "coordinates": {"latitude": lat, "longitude": lon},
                    "susceptibility_probability": None,
                    "frozen_threshold": INDIA_FROZEN_THRESHOLD,
                    "model": INDIA_MODEL_NAME,
        "model_kind": "india",
                    "reason": "Cell has no verified SRTM coverage — no prediction fabricated.",
                    "no_fabricated_predictions": True,
                }
            return {
                "service": "TerraGuard AI API",
                "status": "inference_complete",
                "coordinates": {"latitude": lat, "longitude": lon},
                "susceptibility_probability": float(value),
                "frozen_threshold": INDIA_FROZEN_THRESHOLD,
                "model": INDIA_MODEL_NAME,
        "model_kind": "india",
                "model_note": "Precomputed RF v1-national raster (frozen experimental output, ~1 km grid from 353 audited SRTM tiles). Point lookup, not a live model call.",
                "coverage": "~38% of the national bbox has verified DEM coverage; outside cells return outside_coverage.",
                "overlay": {
                    "png": "/susceptibility/india_overlay.png",
                    "bounds": "/susceptibility/india_overlay.json",
                },
                "no_fabricated_predictions": True,
            }
    except Exception as e:
        return {
            "service": "TerraGuard AI API",
            "status": "rf_unavailable",
            "coordinates": {"latitude": lat, "longitude": lon},
            "susceptibility_probability": None,
            "frozen_threshold": INDIA_FROZEN_THRESHOLD,
            "error": f"RF raster read error: {str(e)[:200]}",
            "no_fabricated_predictions": True,
        }


def handle_india_raster(handler):
    """Serve the India-wide experimental susceptibility GeoTIFF."""
    if not INDIA_RASTER.exists():
        send_error_json(handler, 404, "India susceptibility raster not found")
        return
    body = INDIA_RASTER.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_india_overlay_png(handler):
    """Serve the India-wide susceptibility overlay PNG."""
    if not INDIA_OVERLAY_PNG.exists():
        send_error_json(handler, 404, "India overlay PNG not found")
        return
    body = INDIA_OVERLAY_PNG.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_india_overlay_json(handler):
    """Serve India-wide overlay metadata for Leaflet imageOverlay."""
    if not INDIA_OVERLAY_JSON.exists():
        send_error_json(handler, 404, "India overlay JSON not found")
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
    body = INDIA_OVERLAY_JSON.read_bytes()
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_coverage(handler):
    """GeoJSON FeatureCollection of the 353 audited SRTM 1-degree tiles.

    Used by the frontend to render a coverage footprint layer.
    """
    import csv
    features = []
    try:
        with open(COVERAGE_CSV, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                tile = (row.get("tile") or "").replace(".tif", "")
                status = row.get("manifest_status", "")
                try:
                    pct = float(row.get("valid_pixel_pct") or 0)
                except ValueError:
                    pct = 0.0
                if not (("valid" in status or "existing" in status) and pct > 0):
                    continue
                try:
                    lat = int(tile[1:3])
                    lon = int(tile[4:7])
                except (ValueError, IndexError):
                    continue
                features.append({
                    "type": "Feature",
                    "properties": {"tile": tile, "valid_pixel_pct": round(pct, 1)},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[lon, lat], [lon + 1, lat], [lon + 1, lat + 1], [lon, lat + 1], [lon, lat]]],
                    },
                })
    except FileNotFoundError:
        send_error_json(handler, 404, "Coverage audit not found")
        return
    send_json(handler, {"type": "FeatureCollection", "features": features,
                        "count": len(features)})


def handle_model_info(handler):
    """Report both RF models and actual coverage — machine-readable."""
    india_stats = {}
    try:
        with open(INDIA_OVERLAY_JSON, "r", encoding="utf-8") as f:
            india_stats = json.load(f).get("probability_stats", {})
    except FileNotFoundError:
        india_stats = {}
    send_json(handler, {
        "service": "TerraGuard AI API",
        "national_model": {
            "name": "RF v1-national (rf_india_susceptibility_v1.joblib)",
            "status": "trained_experimental",
            "resolution": "~1 km (0.01-degree EPSG:4326 grid)",
            "features": ["elevation", "slope", "aspect_sin", "aspect_cos", "hillshade"],
            "training_samples": 30911,
            "labels": "GSI national landslide inventory (trigger-agnostic points)",
            "training_protocol": "GroupKFold(n_splits=5) by 1-degree tile; OOF ROC-AUC 0.929, PR-AUC 0.899",
            "frozen_threshold": INDIA_FROZEN_THRESHOLD,
            "coverage": "353 verified SRTM GL1 tiles; ~38% of the national bbox (lon 68-98, lat 6-37)",
            "tif": "/susceptibility/india_raster",
            "overlay_png": "/susceptibility/india_overlay.png",
            "overlay_bounds": "/susceptibility/india_overlay.json",
            "probability_stats": india_stats,
            "no_fabricated_predictions": True,
        },
        "chamoli_pilot": {
            "name": "RF v3 Chamoli pilot (rf_pixel_susceptibility_pipeline_v3.joblib)",
            "status": "trained_experimental",
            "resolution": "~30 m (SRTM derivatives)",
            "training": "49 earthquake-triggered (1999 Mw 6.6) polygons + background samples",
            "frozen_threshold": 0.38,
            "tif": "/susceptibility/raster",
            "overlay_png": "/susceptibility/overlay.png",
            "overlay_bounds": "/susceptibility/overlay.json",
            "scope": "Chamoli district AOI only",
            "no_fabricated_predictions": True,
        },
        "rainfall_screening": {
            "status": "available",
            "note": "Open-Meteo rainfall is separate from RF; screening index is uncalibrated",
        },
        "no_fabricated_predictions": True,
        "no_fabricated_data": True,
    })


def handle_rf_susceptibility(handler):
    """RF susceptibility inference for a point.

    model=india   (default) — RF v1-national raster (~1 km, 353 audited SRTM tiles)
    model=chamoli           — RF v3 Chamoli pilot raster (~30 m, 1999 Mw 6.6 event, EXPERIMENTAL)
    """
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    try:
        lat = float(params.get("lat", [30.33])[0])
        lon = float(params.get("lon", [79.42])[0])
    except (ValueError, TypeError, IndexError):
        send_error_json(handler, 400, "Missing or invalid 'lat'/'lon' query parameters")
        return
    model = (params.get("model", ["india"])[0] or "india").lower()
    if model == "chamoli":
        send_json(handler, rf_susceptibility_point(lat, lon))
    elif model == "india":
        send_json(handler, india_susceptibility_point(lat, lon))
    else:
        send_error_json(handler, 400, "Unknown model — use 'india' (default) or 'chamoli'")


def handle_susceptibility_raster(handler):
    """Serve the experimental susceptibility GeoTIFF."""
    path = SUS_4326
    if not path.exists():
        send_error_json(handler, 404, "Susceptibility raster not found")
        return
    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_susceptibility_overlay_png(handler):
    """Serve the RF v3 overlay PNG for Leaflet imageOverlay."""
    path = OVERLAY_PNG
    if not path.exists():
        send_error_json(handler, 404, "Overlay PNG not found")
        return
    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_susceptibility_overlay_json(handler):
    """Serve overlay metadata: Leaflet imageOverlay bounds + legend."""
    path = OVERLAY_JSON
    if not path.exists():
        send_error_json(handler, 404, "Overlay JSON not found")
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
    body = path.read_bytes()
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_inventory(handler):
    """Serve landslide inventory GeoJSON."""
    inv_path = PROJECT_ROOT / "data" / "processed" / "inventory_chamoli_epsg4326.geojson"
    if not inv_path.exists():
        send_error_json(handler, 404, "Inventory file not found")
        return
    body = inv_path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/geo+json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_screening(handler):
    """Rainfall + terrain SCREENING signal (EXPERIMENTAL — uncalibrated).

    S = clamp01(P_terrain * (1 + alpha * N_rain))
    P_terrain  = RF v3 susceptibility raster value at the point
    N_rain     = max normalized indicator over complete observed (or forecast) windows
    """
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    try:
        lat = float(params.get("lat", [30.33])[0])
        lon = float(params.get("lon", [79.42])[0])
    except (ValueError, TypeError, IndexError):
        send_error_json(handler, 400, "Missing or invalid 'lat'/'lon' query parameters")
        return

    try:
        import screening_utils as su
    except ImportError:
        send_json(handler, {
            "service": "TerraGuard AI API",
            "status": "unavailable",
            "coordinates": {"latitude": lat, "longitude": lon},
            "error": "screening_utils module not available",
        })
        return

    basis = (params.get("basis", ["observed"])[0] or "observed").lower()
    if basis not in ("observed", "forecast"):
        basis = "observed"
    alpha = su.effective_alpha(query_alpha=params.get("alpha", [None])[0])

    try:
        payload, from_cache = su.fetch_open_meteo(lat, lon, force_refresh=False)
        rain_block = su.build_rainfall_block(payload)
    except Exception as e:
        send_json(handler, {
            "service": "TerraGuard AI API",
            "status": "rainfall_unavailable",
            "coordinates": {"latitude": lat, "longitude": lon},
            "terrain": {
                "rf_v3_probability": rf_susceptibility_point(lat, lon).get("susceptibility_probability"),
                "frozen_threshold": FROZEN_THRESHOLD,
            },
            "rainfall": {"status": "unavailable", "error": f"Open-Meteo fetch failed: {str(e)[:150]}",
                         "source": "Open-Meteo API — live request failed"},
            "combined": {"combined_screening_index": None, "alpha": alpha,
                         "rainfall_normalized": None, "rainfall_basis": basis,
                         "reason": "Rainfall unavailable — combined index not computed"},
            "disclaimer": su.DISCLAIMER,
        })
        return

    # Merge anchor hour into both blocks (frontend reads it per block)
    rain_block["observed"]["anchor_hour_ist"] = rain_block.get("anchor_hour_ist")
    rain_block["forecast"]["anchor_hour_ist"] = rain_block.get("anchor_hour_ist")
    rain_block["status"] = rain_block.get("observed", {}).get("status", "partial")
    rain_block["from_cache"] = bool(from_cache)

    model = (params.get("model", ["india"])[0] or "india").lower()
    if model not in ("india", "chamoli"):
        model = "india"
    terrain = india_susceptibility_point(lat, lon) if model == "india" else rf_susceptibility_point(lat, lon)
    tp = terrain.get("susceptibility_probability")
    nr = rain_block[basis].get("normalized_indicator")
    combined = su.combine(tp, nr, alpha)

    if tp is not None and nr is not None:
        combined_status = "computed"
        reason = ""
    elif tp is None:
        combined_status = "terrain_unavailable"
        reason = "terrain susceptibility unavailable (point outside AOI / nodata / model missing)"
    else:
        combined_status = "rainfall_unavailable"
        reason = f"rainfall unavailable — no complete {basis} window"

    send_json(handler, {
        "service": "TerraGuard AI API",
        "status": combined_status,
        "coordinates": {"latitude": lat, "longitude": lon},
        "terrain": {
            "rf_v3_probability": tp,
            "frozen_threshold": (INDIA_FROZEN_THRESHOLD if model == "india" else FROZEN_THRESHOLD),
            "model": (INDIA_MODEL_NAME if model == "india" else RF_MODEL_NAME),
            "model_kind": model,
            "note": ("RF v1-national frozen terrain-only susceptibility; does not include rainfall. Experimental, ~1 km grid."
                     if model == "india" else
                     "RF v3 Chamoli pilot frozen terrain-only susceptibility; does not include rainfall. Experimental."),
            "terrain_status": terrain.get("status"),
        },
        "rainfall": rain_block,
        "combined": {
            "combined_screening_index": combined,
            "alpha": alpha,
            "rainfall_normalized": nr,
            "rainfall_basis": basis,
            "reason": reason,
            "formula": "S = clamp(P_terrain * (1 + alpha * N_rain), 0, 1)",
            "note": "UNCALIBRATED experimental index. NOT a calibrated landslide probability.",
        },
        "disclaimer": su.DISCLAIMER,
        "source": rain_block.get("source"),
        "fetched_at_ist": rain_block.get("fetched_at_ist"),
        "from_cache": bool(from_cache),
        "no_fabricated_predictions": True,
    })


def handle_weather(handler):
    """Fetch weather from Open-Meteo API (full current/hourly/daily parameter set)."""
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    try:
        lat = float(params.get("lat", [30.33])[0])
        lon = float(params.get("lon", [79.42])[0])
    except (ValueError, TypeError, IndexError):
        send_error_json(handler, 400, "Missing or invalid 'lat'/'lon' query parameters")
        return

    current_vars = ("temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation,precipitation_probability,weather_code,"
                    "cloud_cover,surface_pressure,wind_speed_10m,wind_gusts_10m,is_day")
    hourly_vars = ("temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "precipitation,precipitation_probability,weather_code,"
                   "wind_speed_10m,wind_gusts_10m,is_day")
    daily_vars = ("weather_code,temperature_2m_max,temperature_2m_min,"
                  "apparent_temperature_max,apparent_temperature_min,"
                  "precipitation_sum,rain_sum,precipitation_probability_max,wind_speed_10m_max")

    try:
        url = (
            "https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current={current_vars}"
            f"&hourly={hourly_vars}"
            f"&daily={daily_vars}"
            "&forecast_days=7&timezone=Asia/Kolkata"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "TerraGuardAI/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        send_json(handler, {
            "service": "TerraGuard AI API",
            "status": "weather_unavailable",
            "coordinates": {"latitude": lat, "longitude": lon},
            "error": f"Open-Meteo fetch failed: {str(e)[:150]}",
            "no_fabricated_data": True,
        })
        return

    import datetime
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    fetched_at_ist = datetime.datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S IST")

    send_json(handler, {
        "service": "TerraGuard AI API",
        "status": "weather_ok",
        "coordinates": {"latitude": lat, "longitude": lon},
        "timezone": parsed.get("timezone", "Asia/Kolkata"),
        "source": "Open-Meteo API (https://api.open-meteo.com/v1/forecast) — live",
        "fetched_at_ist": fetched_at_ist,
        "from_cache": False,
        "current": parsed.get("current", {}),
        "hourly": parsed.get("hourly", {}),
        "daily": parsed.get("daily", {}),
        "no_fabricated_data": True,
    })


# ── Main Handler Class ────────────────────────────────────────────────────
class TerraGuardHandler(http.server.SimpleHTTPRequestHandler):
    """
    Custom handler that:
    1. Routes API requests (/health, /datasets, etc.) to dedicated handlers
    2. Serves static files from FRONTEND_DIR via SimpleHTTPRequestHandler
    3. Falls back to index.html for SPA-style routing
    """

    # API route table — checked before static serving
    API_ROUTES = {
        "/health", "/datasets", "/layers", "/map-info", "/status",
        "/rf/susceptibility", "/screening", "/weather", "/inventory",
        "/susceptibility/raster", "/susceptibility/overlay.png", "/susceptibility/overlay.json",
        "/susceptibility/india_raster", "/susceptibility/india_overlay.png", "/susceptibility/india_overlay.json",
        "/coverage", "/model-info",
    }

    def __init__(self, *args, **kwargs):
        # Set the directory for SimpleHTTPRequestHandler to FRONTEND_DIR
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    # ── GET handler ─────────────────────────────────────────────────────
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path  # strip query string

        # 1. API routes → delegate to API handlers
        if path in self.API_ROUTES:
            self._handle_api(path)
            return

        # 2. Root "/" → serve index.html
        if path == "/" or path == "":
            self._serve_index()
            return

        # 3. map.html → serve it directly
        if path == "/map.html":
            self._serve_file("map.html", "text/html; charset=utf-8")
            return

        # 4. Explicit asset routes → serve from FRONTEND_DIR with correct MIME type
        asset_map = {
            "/app.js": ("app.js", "application/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        if path in asset_map:
            self._serve_file(asset_map[path][0], asset_map[path][1])
            return

        # 5. /vendor/leaflet/* and /vendor/* → serve local vendor files by absolute path
        if path.startswith("/vendor/"):
            # Translate URL '/vendor/leaflet/leaflet.js' → FRONTEND_DIR/vendor/leaflet/leaflet.js
            rel = path.lstrip("/")
            target = (FRONTEND_DIR / rel).resolve()
            # Safety: prevent path traversal outside FRONTEND_DIR
            if FRONTEND_DIR.resolve() not in target.parents and target != FRONTEND_DIR.resolve():
                self.send_error(403, "Forbidden")
                return
            self._serve_absolute(target, rel)
            return

        # 6. SPA/frontend fallback → index.html for unknown frontend routes,
        #    or normal 404 for anything else
        fallback = FRONTEND_DIR / path.lstrip("/")
        if fallback.suffix and fallback.is_file():
            # Known-extension file that exists → serve it
            self._serve_file(path.lstrip("/"), mimetypes.guess_type(str(fallback))[0] or "application/octet-stream")
            return
        try:
            super().do_GET()
        except Exception as e:
            self.log_error(f"Error serving {self.path}: {e}")
            try:
                send_error_json(self, 500, f"Server error: {e}")
            except Exception:
                pass

    # ── API dispatch ────────────────────────────────────────────────────
    def _handle_api(self, path):
        if path == "/health":
            send_json(self, {
                "service": "TerraGuard AI API",
                "status": "running",
                "version": "1.1.0-ui",
                "ui": {
                    "dashboard": "/",
                    "map": "/map.html",
                    "layout": "map-first dark geospatial dashboard (DRISHTI-inspired)",
                },
                "local_leaflet_path": "vendor/leaflet/leaflet.js",
                "map_layers": [
                    "Esri World Imagery",
                    "Esri World Topo",
                    "Esri World Street",
                    "OpenStreetMap",
                ],
                "actual_downloads": [
                    "NRSC Atlas PDF (33.9MB)",
                    "SRTM DEM tiles",
                    "Terrain derivatives",
                    "NASA IMERG 2000-2020",
                ],
                "rf_model": {
                    "status": "trained_experimental",
                    "national_model": "rf_india_susceptibility_v1.joblib",
                    "national_scope": "India-wide (~1 km grid; 353 audited SRTM tiles; ~38 percent bbox coverage)",
                    "national_roc_auc_oof": 0.929,
                    "national_pr_auc_oof": 0.899,
                    "frozen_threshold": 0.402,
                    "chamoli_pilot": "rf_pixel_susceptibility_pipeline_v3.joblib",
                    "chamoli_threshold": 0.38,
                    "note": "India-wide RF default; Chamoli pilot optional. EXPERIMENTAL - NOT operational",
                },
                "susceptibility_raster": {
                    "available": True,
                    "geotiff": "/susceptibility/india_raster",
                    "overlay_png": "/susceptibility/india_overlay.png",
                    "overlay_bounds": "/susceptibility/india_overlay.json",
                    "label": "EXPERIMENTAL-INDIA-WIDE",
                },
                "chamoli_pilot_raster": {
                    "available": True,
                    "geotiff": "/susceptibility/raster",
                    "overlay_png": "/susceptibility/overlay.png",
                    "overlay_bounds": "/susceptibility/overlay.json",
                },
                "coverage_geojson": "/coverage",
                "model_info": "/model-info",
                "inventory_geojson": "/inventory",
                "rainfall_screening": {
                    "available": True,
                    "endpoint": "/screening?lat=&lon=&alpha=&basis=",
                    "label": "EXPERIMENTAL - UNCALIBRATED",
                    "note": "Combined terrain (RF v3) + normalized Open-Meteo rainfall screening index; NOT a calibrated probability",
                },
                "manual_downloads_pending": [],
                "pilot_region": {
                    "district": "Chamoli",
                    "state": "Uttarakhand",
                    "extent_approx": "N30-N31, E78-E80",
                },
                "no_fabricated_predictions": True,
                "no_fabricated_data": True,
            })

        elif path == "/datasets":
            send_json(self, {
                "message": "Only verified dataset evidence reported",
                "downloaded_and_verified": [
                    {"name": "NRSC Landslide Atlas PDF", "path": "data/raw/landslide_inventory/", "status": "downloaded_not_extracted"},
                    {"name": "IIT-Roorkee Chamoli inventory (73 polygons, EPSG:32644)", "path": "data/raw/landslide_inventory/Landslide_chamoli.shp", "status": "present"},
                    {"name": "Clipped study-area inventory (49 polygons)", "path": "data/processed/clipped_earthquake_inventory.gpkg", "status": "processed_verified"},
                    {"name": "Inventory GeoJSON (EPSG:4326, for map)", "path": "data/processed/inventory_chamoli_epsg4326.geojson", "status": "processed_verified"},
                    {"name": "SRTM DEM tiles", "path": "data/raw/dem/", "status": "present"},
                    {"name": "DEM mosaic + projected mosaic", "path": "data/processed/dem/", "status": "computed_verified"},
                    {"name": "Terrain derivatives (slope/aspect/hillshade)", "path": "data/processed/terrain_corrected/", "status": "computed_verified"},
                    {"name": "NASA IMERG monthly 2000-2020 (zips)", "path": "data/raw/rainfall/", "status": "present_downloaded"},
                    {"name": "RF v3 district model (selected)", "path": "models/rf_pixel_susceptibility_pipeline_v3.joblib", "status": "trained_experimental"},
                    {"name": "RF v4 expanded-AOI model (experiment, rejected)", "path": "reports/rf_v4_experiments.{json,txt}", "status": "evaluated_not_selected"},
                    {"name": "Experimental susceptibility GeoTIFF", "path": "data/processed/susceptibility_chamoli_experimental.tif", "status": "computed_experimental"},
                ],
                "pending_manual": [
                    {"name": "GSI geology/lithology layers", "note": "Not acquired this session (no network egress; WMS access unverified)"},
                    {"name": "OSM roads/rivers", "note": "Overpass API unreachable this session (502/timeouts)"},
                ],
            })

        elif path == "/layers":
            send_json(self, {
                "satellite_layer": {
                    "url_template": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                    "verified": True,
                    "attribution": "Tiles &copy; Esri",
                },
                "topographic_layer": {
                    "url_template": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
                    "verified": True,
                    "attribution": "Tiles &copy; Esri",
                },
            })

        elif path == "/map-info":
            send_json(self, {
                "local_leaflet_path": "vendor/leaflet/leaflet.js",
                "local_leaflet_css": "vendor/leaflet/leaflet.css",
                "local_marker_images": "vendor/leaflet/images/",
                "map_container_height": "400px",
                "initial_center": [22.5, 79.0],
                "initial_zoom": 5,
                "coordinate_validation": "6/6 passing",
                "no_fabricated_markers": True,
                "no_fabricated_predictions": True,
            })

        elif path == "/status":
            send_json(self, {
                "message": "Sprint 2 complete: real dataset acquired (NRSC PDF 33.9MB verified), local leaflet preserved, ArcGIS satellite and topographic tiles verified, map navigation working. USGS DEM and NASA IMERG downloads require manual user login.",
                "data_directory_exists": (PROJECT_ROOT / "data").exists(),
                "metadata_directory_exists": (PROJECT_ROOT / "data" / "metadata").exists(),
            })

        elif path == "/rf/susceptibility":
            handle_rf_susceptibility(self)

        elif path == "/susceptibility/raster":
            handle_susceptibility_raster(self)

        elif path == "/susceptibility/overlay.png":
            handle_susceptibility_overlay_png(self)

        elif path == "/susceptibility/overlay.json":
            handle_susceptibility_overlay_json(self)

        elif path == "/susceptibility/india_raster":
            handle_india_raster(self)

        elif path == "/susceptibility/india_overlay.png":
            handle_india_overlay_png(self)

        elif path == "/susceptibility/india_overlay.json":
            handle_india_overlay_json(self)

        elif path == "/coverage":
            handle_coverage(self)

        elif path == "/model-info":
            handle_model_info(self)

        elif path == "/inventory":
            handle_inventory(self)

        elif path == "/screening":
            handle_screening(self)

        elif path == "/weather":
            handle_weather(self)

    # ── File serving helpers ───────────────────────────────────────────
    def _serve_absolute(self, abs_path, rel_display):
        """Serve a file at an absolute path (used for safe /vendor/* paths)."""
        if not abs_path.exists() or not abs_path.is_file():
            self.send_error(404, f"File not found: {rel_display}")
            return
        try:
            body = abs_path.read_bytes()
        except OSError as e:
            send_error_json(self, 500, f"Cannot read {rel_display}: {e}")
            return
        ctype = mimetypes.guess_type(str(abs_path))[0] or "application/octet-stream"
        if abs_path.suffix.lower() in (".js", ".mjs"):
            ctype = "application/javascript; charset=utf-8"
        elif abs_path.suffix.lower() == ".css":
            ctype = "text/css; charset=utf-8"
        elif abs_path.suffix.lower() == ".png":
            ctype = "image/png"
        elif abs_path.suffix.lower() in (".jpg", ".jpeg"):
            ctype = "image/jpeg"
        elif abs_path.suffix.lower() == ".svg":
            ctype = "image/svg+xml"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _serve_index(self):
        """Serve index.html for the root path with a real HTML body."""
        index_path = FRONTEND_DIR / "index.html"
        if index_path.exists() and index_path.is_file():
            self._serve_file("index.html", "text/html; charset=utf-8")
        else:
            # Fallback: serve inline HTML if file is missing
            html = b"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>TerraGuardAI</title></head>
<body><h1>TerraGuardAI API Server</h1>
<p>Frontend index.html not found at frontend/public/index.html</p>
<p>API endpoints: /health, /datasets, /layers, /map-info, /status, /inventory</p>
</body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(html)

    def _serve_file(self, rel_path, content_type):
        """Serve a file from FRONTEND_DIR with explicit content type."""
        file_path = FRONTEND_DIR / rel_path
        if not file_path.exists() or not file_path.is_file():
            send_error_json(self, 404, f"File not found: {rel_path}")
            return
        try:
            body = file_path.read_bytes()
        except OSError as e:
            send_error_json(self, 500, f"Cannot read {rel_path}: {e}")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    # ── Logging ────────────────────────────────────────────────────────
    def log_message(self, format, *args):
        """Suppress noisy per-request logging."""
        pass


# ── Server startup ────────────────────────────────────────────────────────
class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def main():
    port = 5000
    with ReusableTCPServer(("", port), TerraGuardHandler) as httpd:
        print("=" * 60)
        print("  TerraGuardAI Server")
        print("=" * 60)
        print(f"  Running at:  http://localhost:{port}")
        print(f"  Frontend:    {FRONTEND_DIR}")
        print(f"  Dashboard:   http://localhost:{port}/")
        print(f"  Map page:    http://localhost:{port}/map.html")
        print()
        print("  API endpoints:")
        print("    /health")
        print("    /datasets")
        print("    /layers")
        print("    /map-info")
        print("    /status")
        print("    /rf/susceptibility?lat=30.33&lon=79.42")
        print("    /screening?lat=30.33&lon=79.42")
        print("    /weather?lat=30.33&lon=79.42")
        print("    /inventory")
        print()
        print("  WARNING: Susceptibility outputs are EXPERIMENTAL research,")
        print("           not hazard forecasts.")
        print("=" * 60)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
