#!/usr/bin/env python3
"""
TerraGuardAI — Shared Open-Meteo client with a thread-safe TTL cache.

Design goals:
- Weather display (/weather) and rainfall screening (/screening) reuse one
  cached Open-Meteo response per ~1.1 km cell whenever fresh enough.
- Concurrent requests NEVER trigger parallel upstream fetches (singleflight).
- Successful responses cached for CACHE_TTL_S seconds (12 minutes).
- HTTP 429 (rate limit) handled with exponential backoff + Retry-After,
  capped to a small number of attempts — never retried indefinitely.
- On upstream failure, the last cached payload is reused and returned with
  fetched_at/ stale=True (if available). If nothing is cached, the error
  surfaces so rainfall stays 'unavailable' instead of fake 0 mm.
- Rainfall is NEVER replaced by 0 / estimates / invented values.
"""

import datetime
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("terraguard.om")

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

CACHE_TTL_S = 12 * 60          # 12 minutes — within 10–15 min target
REQUEST_TIMEOUT_S = 15
MAX_ATTEMPTS = 3               # total upstream attempts (initial + 2 retries)
BASE_BACKOFF_S = 1.0           # 1s, 2s, 4s (clamped by Retry-After when present)
MAX_BACKOFF_S = 8.0            # cap any single sleep

# One unified parameter set — serves both /weather (full display set) and
# /screening (precipitation windows). past_days=7 + forecast_days=7.
CURRENT_VARS = ("temperature_2m,apparent_temperature,relative_humidity_2m,"
                "precipitation,rain,showers,precipitation_probability,"
                "weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_gusts_10m,is_day")
HOURLY_VARS = ("temperature_2m,apparent_temperature,relative_humidity_2m,"
               "precipitation,rain,showers,precipitation_probability,weather_code,"
               "wind_speed_10m,wind_gusts_10m,is_day")
DAILY_VARS = ("weather_code,temperature_2m_max,temperature_2m_min,"
              "apparent_temperature_max,apparent_temperature_min,"
              "precipitation_sum,rain_sum,precipitation_probability_max,wind_speed_10m_max")


def _key(lat, lon):
    return (round(float(lat), 2), round(float(lon), 2))


_cache = {}          # key -> {"fetched_epoch": float, "payload": dict}
_cache_lock = threading.Lock()
_fetch_lock = threading.Lock()   # singleflight: only one in-flight upstream fetch


def _read_retry_after(headers):
    try:
        raw = (headers or {}).get("Retry-After")
        if not raw:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _request_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "TerraGuardAI/1.0"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_with_backoff(url):
    """Single upstream GET with bounded retries. Returns parsed JSON.
    Raises on unrecoverable failure (caller maps to cache-or-error)."""
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _request_json(url)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 or e.code >= 500:
                retry_after = _read_retry_after(getattr(e, "headers", None))
                backoff = retry_after if retry_after is not None else min(BASE_BACKOFF_S * (2 ** (attempt - 1)), MAX_BACKOFF_S)
                if attempt < MAX_ATTEMPTS:
                    wait = min(backoff + 0.0, MAX_BACKOFF_S)
                    log.warning("Open-Meteo HTTP %s (attempt %d/%d); sleeping %.1fs%s",
                                e.code, attempt, MAX_ATTEMPTS, wait,
                                f" (Retry-After={retry_after}s)" if retry_after is not None else "")
                    time.sleep(wait)
                    continue
                log.error("Open-Meteo HTTP %s after %d attempts — giving up", e.code, MAX_ATTEMPTS)
                raise
            # 4xx (non-429): not retryable
            log.error("Open-Meteo HTTP %s (non-retryable)", e.code)
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            last_err = e
            if attempt < MAX_ATTEMPTS:
                wait = min(BASE_BACKOFF_S * (2 ** (attempt - 1)), MAX_BACKOFF_S)
                log.warning("Open-Meteo fetch error (attempt %d/%d): %s; sleeping %.1fs",
                            attempt, MAX_ATTEMPTS, e, wait)
                time.sleep(wait)
                continue
            log.error("Open-Meteo fetch failed after %d attempts: %s", MAX_ATTEMPTS, e)
            raise
    raise last_err  # pragma: no cover


def fetch_forecast(lat, lon):
    """Return (payload, from_cache, fetched_epoch, stale).

    from_cache — False only when a brand-new upstream fetch happened at this call.
    stale      — True only when the returned payload is the last known copy used
                 after an upstream failure (timestamp via fetched_epoch).
    Raises OSError on failure when no cached entry exists.
    """
    k = _key(lat, lon)
    now = time.time()

    # Fast path: fresh cache hit
    with _cache_lock:
        entry = _cache.get(k)
        if entry and (now - entry["fetched_epoch"]) < CACHE_TTL_S:
            log.info("open_meteo cache HIT key=(%.2f,%.2f) age=%.0fs", k[0], k[1], now - entry["fetched_epoch"])
            return entry["payload"], True, entry["fetched_epoch"], False

    # Singleflight: serialize upstream fetches; re-check cache on entry
    with _fetch_lock:
        with _cache_lock:
            entry = _cache.get(k)
            if entry and (now - entry["fetched_epoch"]) < CACHE_TTL_S:
                log.info("open_meteo cache HIT (singleflight) key=(%.2f,%.2f) age=%.0fs",
                         k[0], k[1], now - entry["fetched_epoch"])
                return entry["payload"], True, entry["fetched_epoch"], False

        params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "current": CURRENT_VARS,
            "hourly": HOURLY_VARS,
            "daily": DAILY_VARS,
            "past_days": "7",
            "forecast_days": "7",
            "timezone": "Asia/Kolkata",
        })
        url = f"{OPEN_METEO_BASE}?{params}"
        try:
            payload = _fetch_with_backoff(url)
            with _cache_lock:
                _cache[k] = {"fetched_epoch": time.time(), "payload": payload}
            log.info("open_meteo cache MISS key=(%.2f,%.2f) upstream OK", k[0], k[1])
            return payload, False, time.time(), False
        except Exception as e:
            # Upstream failed — fall back to cached data only if it exists (any age)
            with _cache_lock:
                entry = _cache.get(k)
            if entry:
                age = time.time() - entry["fetched_epoch"]
                log.warning("open_meteo stale FALLBACK key=(%.2f,%.2f) age=%.0fs reason=%s",
                            k[0], k[1], age, e)
                return entry["payload"], True, entry["fetched_epoch"], True
            log.error("open_meteo upstream error and NO cache: key=(%.2f,%.2f) error=%s",
                      k[0], k[1], e)
            raise


def iso_ist_from_epoch(epoch):
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime.fromtimestamp(epoch, ist).strftime("%Y-%m-%d %H:%M:%S IST")
