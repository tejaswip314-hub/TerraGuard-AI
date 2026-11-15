#!/usr/bin/env python
"""
Rainfall normalization + combined landslide SCREENING signal.

EXPERIMENTAL — UNCALIBRATED. This module is NOT a trained model and produces NO
calibrated landslide probability. RF v3 (terrain susceptibility) is used exactly as
frozen by the sprint; rainfall is incorporated separately and transparently.

EXACT FORMULAS (also see docs/RAINFALL_SCREENING.md)
----------------------------------------------------
1. Window accumulations (from real Open-Meteo hourly precipitation, mm):
     A_k = sum of hourly precipitation over trailing window k
   where k in {1h, 3h, 24h, 72h, 7d} for OBSERVED windows (trailing window ending
   at the last complete hour; the current, partial hour is EXCLUDED),
   and k in {1h, 3h, 24h} for FORECAST windows (next 1h/3h/24h).

2. Per-window normalized indicator (monotonic, in [0, 1); N = 0.5 at A = R_k):
     N_k = A_k / (A_k + R_k)
   R_k = reference rainfall (mm) for window k — ILLUSTRATIVE, UNCALIBRATED
   (defaults: 10 / 25 / 80 / 150 / 250 mm; configurable, see REFERENCE_MM).

3. Overall normalized rainfall indicator (worst complete window dominates):
     N_rain = max over COMPLETE windows of N_k        # in [0, 1)
   A window is COMPLETE iff every hour in it has a non-null measured value.
   Incomplete (partial) windows are FLAGGED and EXCLUDED from N_rain.
   If no window is complete, N_rain = None.

4. Combined screening index (bounded 0-1 for display):
     S = clamp( P_terrain * (1 + alpha * N_rain), 0, 1 )
   alpha default 1.0 — UNCALIBRATED, configurable (query param or RAIN_ALPHA env).
   P_terrain is the RF v3 probability, returned UNCHANGED.

   If rainfall is unavailable (no complete window), S = None — never fabricated.
   If terrain is unavailable, S = None. Zero rainfall (complete data) gives
   N_rain = 0 and therefore S = P_terrain exactly.

MISSING DATA POLICY
-------------------
Hours that are absent or null are NEVER replaced with zeros. Missing hours are
reported explicitly (missing_hours per window, partial_windows list, and an
'unavailable' status when nothing complete exists). Chamoli example references
below are NOT derived from Chamoli landslide data.
"""

import datetime
import os
import threading
import time
import urllib.parse
import urllib.request

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_S = 20
CACHE_TTL_S = 300.0  # avoid hammering Open-Meteo on rapid map clicks

# ── Illustrative, UNCALIBRATED reference rainfall amounts (mm) ────────────────
# At A == R_k the per-window indicator N_k == 0.5. Values are chosen to be
# physically plausible for a Himalayan monsoon/extreme-event context and are the
# SAME order of magnitude as IMERG monthly extremes (see reports), but they are
# NOT validated against Chamoli landslide occurrence and MUST NOT be used as
# operational thresholds without site-specific calibration.
REFERENCE_MM = {
    '1h': 10.0,
    '3h': 25.0,
    '24h': 80.0,
    '72h': 150.0,
    '7d': 250.0,
}
WINDOW_HOURS = {'1h': 1, '3h': 3, '24h': 24, '72h': 72, '7d': 168}
FORECAST_WINDOWS = ('1h', '3h', '24h')  # forecast horizon provided by Open-Meteo (72h/7d too long)

# Combination coefficient — UNCALIBRATED; configurable via env RAIN_ALPHA.
ALPHA = float(os.environ.get('RAIN_ALPHA', '1.0'))

# Required user-facing disclaimer (kept verbatim in API and UI)
DISCLAIMER = [
    "EXPERIMENTAL RAINFALL-ENHANCED SCREENING INDEX — UNCALIBRATED.",
    "The underlying RF v3 model estimates terrain susceptibility only.",
    "Rainfall is incorporated separately using an illustrative normalization and combination formula.",
    "This is NOT a calibrated landslide probability, trained rainfall-aware model, or operational warning.",
]


def _env_reference(window, default):
    """Effective reference for a window: env RAIN_REF_<KEY> overrides the default."""
    env_name = 'RAIN_REF_' + window.replace('7d', '7D').upper()
    try:
        v = float(os.environ.get(env_name, str(default)))
        return v if v > 0 else default
    except ValueError:
        return default


def reference_mm(window):
    return _env_reference(window, REFERENCE_MM[window])


def clamp01(x):
    return float(max(0.0, min(1.0, x)))


def normalize_window(accumulation_mm, ref_mm):
    """N = A / (A + R). Monotonic, in [0, 1). None if accumulation missing."""
    if accumulation_mm is None:
        return None
    a = float(accumulation_mm)
    r = float(ref_mm)
    if a < 0:
        return 0.0
    return a / (a + r) if (a + r) > 0 else 0.0


def combine(terrain_probability, rainfall_normalized, alpha):
    """S = clamp01( P_terrain * (1 + alpha * N_rain) ). None if either input missing."""
    if terrain_probability is None or rainfall_normalized is None:
        return None
    return clamp01(float(terrain_probability) * (1.0 + float(alpha) * float(rainfall_normalized)))


def effective_alpha(query_alpha=None):
    """Query override (clamped to [0, 10]) or RAIN_ALPHA env or default 1.0."""
    if query_alpha is not None:
        try:
            return float(max(0.0, min(10.0, float(query_alpha))))
        except (TypeError, ValueError):
            return ALPHA
    return ALPHA


# ── Hourly time handling ─────────────────────────────────────────────────────
def _fmt_hour(dt):
    return dt.strftime('%Y-%m-%dT%H:00')


def _parse_hour(s):
    return datetime.datetime.strptime(s, '%Y-%m-%dT%H:%M')


def build_hour_map(hourly_time, hourly_precip):
    """time-string -> precipitation (float or None). Invalid entries -> None (never zero)."""
    m = {}
    for t, p in zip(hourly_time or [], hourly_precip or []):
        if t is None:
            continue
        try:
            v = None if p is None else float(p)
        except (TypeError, ValueError):
            v = None
        m[t] = v
    return m


def _window_hours_complete(hour_map, start_hour, n_hours):
    """Compute window from hour_map starting at start_hour (datetime)."""
    expected = n_hours
    present = 0
    acc = 0.0
    missing = []
    for i in range(n_hours):
        h = start_hour + datetime.timedelta(hours=i)
        key = _fmt_hour(h)
        v = hour_map.get(key)
        if v is None:
            missing.append(key)
        else:
            present += 1
            acc += v
    complete = (present == expected)
    status = 'ok' if complete else ('unavailable' if present == 0 else 'partial')
    return {
        'accumulation_mm': (round(acc, 3) if present > 0 else None),
        'hours_expected': expected,
        'hours_present': present,
        'missing_hours': len(missing),
        'missing_timestamps': missing,
        'complete': complete,
        'status': status,
    }


def compute_windows(hour_map, anchor_hour, window_hours, forecast=False):
    """window name -> window dict (with reference + normalized where complete)."""
    out = {}
    if forecast:
        start = anchor_hour + datetime.timedelta(hours=1)
        kinds = FORECAST_WINDOWS
    else:
        # trailing windows ending at the last complete hour
        start = None  # computed per window
        kinds = tuple(window_hours.keys())
    for name, n in window_hours.items():
        if forecast and name not in kinds:
            continue
        if forecast:
            w = _window_hours_complete(hour_map, start, n)
        else:
            last_complete = anchor_hour - datetime.timedelta(hours=1)
            w = _window_hours_complete(hour_map, last_complete - datetime.timedelta(hours=n - 1), n)
        w['window_h'] = n
        w['reference_mm'] = reference_mm(name)
        w['normalized'] = round(normalize_window(w['accumulation_mm'], w['reference_mm']), 4) \
            if w['complete'] else (round(normalize_window(w['accumulation_mm'], w['reference_mm']), 4)
                                     if w['accumulation_mm'] is not None else None)
        out[name] = w
    return out


def indicator(windows):
    """N_rain = max over complete windows. None if no complete window."""
    complete = [w['normalized'] for w in windows.values() if w.get('complete') and w['normalized'] is not None]
    partial = [name for name, w in windows.items() if w['status'] == 'partial']
    missing = [name for name, w in windows.items() if w['status'] == 'unavailable']
    if not complete:
        return None, {'complete_windows': [], 'partial_windows': partial, 'unavailable_windows': missing,
                      'status': 'unavailable'}
    return round(max(complete), 4), {
        'complete_windows': [name for name, w in windows.items() if w.get('complete')],
        'partial_windows': partial,
        'unavailable_windows': missing,
        'status': 'ok' if not (partial or missing) else 'partial',
    }


# ── Open-Meteo fetch (with TTL cache; 1 request per ~5 min per ~1.1 km cell) ──
_cache_lock = threading.Lock()
_cache = {}  # (lat2, lon2) -> (fetched_epoch, payload)


def fetch_open_meteo(lat, lon, force_refresh=False):
    """Fetch raw Open-Meteo JSON with past_days=7 + forecast_days=3, 5-minute TTL cache.

    Raises urllib.error on network failure; callers must handle it and report
    rainfall as unavailable (never zero-filled).
    """
    key = (round(float(lat), 2), round(float(lon), 2))
    now = time.time()
    if not force_refresh:
        with _cache_lock:
            entry = _cache.get(key)
            if entry and (now - entry[0]) < CACHE_TTL_S:
                return entry[1], True  # cached
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "current": "precipitation,rain,showers,precipitation_probability,wind_gusts_10m",
        "hourly": "precipitation,rain,showers,precipitation_probability",
        "daily": "precipitation_sum,rain_sum,precipitation_probability_max",
        "past_days": "7",
        "forecast_days": "3",
        "timezone": "Asia/Kolkata",
    })
    url = f"{OPEN_METEO_BASE}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "TerraGuardAI/1.0"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as response:
        data = response.read().decode("utf-8")
    import json
    payload = json.loads(data)
    with _cache_lock:
        _cache[key] = (now, payload)
    return payload, False


def parse_anchor_hour(payload):
    """Anchor = the hour containing 'current.time' (floor), as datetime."""
    cur = (payload.get('current') or {}).get('time') or datetime.datetime.now().strftime('%Y-%m-%dT%H:00')
    return _parse_hour(cur[:13] + ':00')


def build_rainfall_block(payload, now_ist=None):
    """Assemble the rainfall block of the screening payload from raw Open-Meteo JSON."""
    hourly = payload.get('hourly') or {}
    current = payload.get('current') or {}
    hour_map = build_hour_map(hourly.get('time'), hourly.get('precipitation'))
    anchor = parse_anchor_hour(payload)
    if now_ist is None:
        now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

    obs_windows = compute_windows(hour_map, anchor, WINDOW_HOURS, forecast=False)
    fct_windows = compute_windows(hour_map, anchor, WINDOW_HOURS, forecast=True)

    obs_indicator, obs_meta = indicator(obs_windows)
    fct_indicator, fct_meta = indicator(fct_windows)

    return {
        "source": "Open-Meteo API (https://api.open-meteo.com/v1/forecast) — live",
        "fetched_at_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "timezone": payload.get("timezone", "Asia/Kolkata"),
        "anchor_hour_ist": _fmt_hour(anchor),
        "current": {
            "time": current.get("time"),
            "precipitation_mm": current.get("precipitation"),
            "rain_mm": current.get("rain"),
            "showers_mm": current.get("showers"),
            "precipitation_probability": current.get("precipitation_probability"),
            "wind_gusts_10m": current.get("wind_gusts_10m"),
        },
        "observed": {
            "kind": "observed (trailing windows, last complete hour; current partial hour excluded)",
            "windows": obs_windows,
            "normalized_indicator": obs_indicator,
            "indicator_basis": "max over complete windows of N = A / (A + R); incomplete windows excluded",
            **obs_meta,
        },
        "forecast": {
            "kind": "forecast (next 1h/3h/24h from hourly forecast)",
            "windows": fct_windows,
            "normalized_indicator": fct_indicator,
            "indicator_basis": "max over complete windows of N = A / (A + R); incomplete windows excluded",
            **fct_meta,
        },
        "missing_data_policy": ("Missing hours are NEVER replaced with 0. Incomplete windows are flagged "
                                 "and excluded from the indicator. If no complete window exists, the indicator is null."),
        "reference_note": ("Reference values (1h 10 mm, 3h 25 mm, 24h 80 mm, 72h 150 mm, 7d 250 mm) are "
                            "ILLUSTRATIVE and UNCALIBRATED defaults; override with RAIN_REF_<WINDOW> env vars. "
                            "They are NOT Chamoli landslide thresholds."),
    }
