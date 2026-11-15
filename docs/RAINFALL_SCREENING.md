# Rainfall-Normalized Landslide Screening Signal — Implementation & Method

**Status: EXPERIMENTAL — UNCALIBRATED. Implemented 2026-11-12.**
**Scope: one transparent screening index, computed at query time from (a) the frozen RF v3 terrain
susceptibility score and (b) live Open-Meteo rainfall. It is NOT a trained rainfall-aware model and
NOT a calibrated landslide probability.**

---

## 1. What was built (and what was deliberately not built)

| Component | Status |
|---|---|
| RF v3 terrain model (`models/rf_pixel_susceptibility_pipeline_v3.joblib`) | **Frozen — not retrained, not modified, not re-weighted** (SHA-256 `CC81FA0A7A5A34B0D1ED758179AC42F564A579F3A5E1127389C4B850E742FBCB`, mtime 2026-11-11 21:36:50) |
| RF v3 features / training data / threshold | **Unchanged** — `[elevation, slope, aspect_sin, aspect_cos, hillshade]`, T = 0.38 |
| Rainfall as an RF predictor | **No** — rainfall never enters the model; `rainfall_is_rf_predictor: false` in every payload |
| Rainfall normalization + combination | **New**, transparent, documented below (`api/screening_utils.py`) |
| New endpoint | `GET /screening?lat=&lon=&alpha=&basis=` |
| Existing endpoints | Unchanged and still working (`/rf/susceptibility` payload is byte-shape compatible with before) |
| Frontends | `public/index.html` and `public/map.html` both show terrain score, all rainfall accumulations, the normalized indicator, the combined index, the reference assumptions, and the mandated disclaimer |

### Data flow

```
RF v3 frozen artifact ──► _terrain_inference(lat,lon) ──► P_terrain  (0–1, UNCHANGED, also returned alone)
                                                              │
Open-Meteo (live, past_days=7, forecast_days=3, 5-min cache)  │
        │                                                     │
        ├─ hourly precipitation (mm, Asia/Kolkata)             │
        │                                                     ▼
        ├─ observed windows 1h/3h/24h/72h/7d ──► N_k = A_k/(A_k+R_k) ──► N_rain = max(complete N_k)
        └─ forecast windows 1h/3h/24h         ──► N_k (separate, never mixed into observed)
                                                              │
                                                              ▼
                              S = clamp01( P_terrain × (1 + α × N_rain) )   [α illustrative, UNCALIBRATED]
```

---

## 2. Exact formulas

### 2.1 Window accumulation

For each window *k*:

```
A_k = Σ hourly_precipitation  over the hours inside window k        [mm]
```

- **Observed** windows: trailing windows ending at the **last complete hour** (the current, in-progress
  hour is excluded). Windows: `1h, 3h, 24h, 72h, 7d (168 h)`.
- **Forecast** windows: the next `1h, 3h, 24h` after the anchor hour (Open-Meteo's hourly forecast;
  no 72 h/7 d forecast is claimed).

### 2.2 Per-window normalized indicator (monotonic, bounded)

```
N_k = A_k / (A_k + R_k)            ∈ [0, 1)
```

Properties: `N_k = 0` when `A_k = 0`; `N_k = 0.5` when `A_k = R_k`; strictly increasing in `A_k`;
asymptotically approaches 1 but never reaches it. This is a **saturating ratio**, not a probability
and not a percentile of any fitted distribution.

### 2.3 Overall normalized rainfall indicator

```
N_rain = max over COMPLETE windows of N_k           # worst-window-dominates
```

- A window is **complete** only if every hour in it has a non-null measured value.
- Incomplete windows are **flagged** (`partial` / `unavailable`, with `hours_present`,
  `missing_hours`, `missing_timestamps`) and **excluded** from `N_rain`.
- If no window is complete → `N_rain = null` (never 0).
- `max` (rather than an average) was chosen so that a short, intense burst is not diluted by a dry week.

### 2.4 Combined screening index

```
S = clamp( P_terrain × (1 + α × N_rain), 0, 1 )
```

- `P_terrain` = RF v3 probability **as returned by the frozen model**, unchanged and separately accessible.
- `α` (alpha): **illustrative, UNCALIBRATED** amplification coefficient. Default **1.0**;
  overridable via `?alpha=` (clamped to `[0, 10]`) or the `RAIN_ALPHA` env var. Not tuned on any data.
- Zero rainfall with complete data ⇒ `N_rain = 0` ⇒ `S = P_terrain` exactly.
- `α = 0` ⇒ `S = P_terrain` regardless of rainfall (useful for ablation).
- Either input missing ⇒ `S = null` with an explicit `reason`.

**Why the clamp matters:** with `P_terrain = 0.9984` and any non-trivial `N_rain`, the product exceeds 1.
The reported value is saturated at 1.0; the UI says "not computed / saturated" semantics via the raw
formula text, and `bounded_0_1: true` marks the bound. Saturation is honest: it means "top of the
screening scale", not "certainty".

---

## 3. Reference rainfall values (ILLUSTRATIVE, UNCALIBRATED)

| Window | `R_k` default | Override env var | Rationale (not calibration) |
|---|---|---|---|
| 1 h | 10 mm | `RAIN_REF_1H` | order of magnitude of an intense Himalayan convective hour |
| 3 h | 25 mm | `RAIN_REF_3H` | short-duration cloudburst-scale burst |
| 24 h | 80 mm | `RAIN_REF_24H` | "heavy rain" day in the Uttarakhand monsoon belt |
| 72 h | 150 mm | `RAIN_REF_72H` | multi-day antecedent wetting |
| 7 d | 250 mm | `RAIN_REF_7D` | prolonged monsoon accumulation |

**Limitations of these references (stated in the API as `reference_note`):**

- They are **chosen to be physically plausible and round**, not fitted to landslide occurrence.
- They are **not** Chamoli landslide-triggering thresholds and are **not** derived from the 1999 event
  (that event was earthquake-triggered, and no reliable sub-daily rainfall record for it is in hand).
- They are **not** validated against IMERG, station data, or any inventory in this repository.
- Changing them rescales `N_rain` but does not make the index calibrated. Any operational use would
  require regional frequency analysis (e.g. IMD/GSI rainfall thresholds) and independent validation.

Because `N_k = 0.5` exactly at `A_k = R_k`, the references can be read plainly as
"the accumulation at which this window is considered half-saturated".

---

## 4. Missing-data policy (never zero-filled)

- Absent or `null` hourly values are **never** replaced with 0 — neither in the accumulation
  (`accumulation_mm` becomes `null` if *no* hour is present) nor in any derived value.
- Each window reports `hours_expected`, `hours_present`, `missing_hours`, `missing_timestamps`,
  `complete` (bool), and `status ∈ {ok, partial, unavailable}`.
- Partial windows are shown in the UI with a warning and **excluded** from `N_rain`.
- If Open-Meteo itself fails (network/timeout) or returns nothing usable:
  `rainfall.status = "unavailable"`, `N_rain = null`, `S = null`,
  overall status `screening_rainfall_unavailable`, and the reason is exposed.
- If the point is outside the RF v3 AOI: `terrain.rf_v3_probability = null`,
  overall status `screening_terrain_out_of_coverage`, `S = null` —
  rainfall is still reported (it is a valid independent measurement).

---

## 5. Observed vs forecast (kept strictly separate)

| | Observed | Forecast |
|---|---|---|
| Windows | 1 h, 3 h, 24 h, 72 h, 7 d | 1 h, 3 h, 24 h |
| Anchor | last **complete** hour (`anchor_hour_ist - 1 h`) | hour after `anchor_hour_ist` |
| Used in `S` by default | **yes** | no (opt-in via `?basis=forecast`) |
| Payload key | `rainfall.observed` | `rainfall.forecast` |

`combined.rainfall_basis` always states which one was used, and the UI labels the two blocks
**OBSERVED** / **FORECAST** with distinct badges. The two are never mixed or averaged.

---

## 6. API contract

`GET /screening?lat=<float>&lon=<float>&alpha=<float>&basis=observed|forecast`

```jsonc
{
  "service": "TerraGuard AI API",
  "status": "screening_complete",          // | screening_terrain_out_of_coverage
                                           // | screening_terrain_unavailable
                                           // | screening_rainfall_unavailable
  "coordinates": {"latitude": 30.30484, "longitude": 79.37097},
  "terrain": {
    "status": "inference_complete",
    "model": "rf_pixel_susceptibility_pipeline_v3.joblib",
    "rf_v3_probability": 0.9984,           // UNCHANGED frozen-model output
    "susceptibility_class": "HIGH",
    "frozen_threshold": 0.38,
    "terrain_score_unchanged": true,
    "metrics_at_frozen_threshold": { "roc_auc": 0.7004, "pr_auc": 0.0908, "...": "..." }
  },
  "rainfall": {
    "source": "Open-Meteo API (https://api.open-meteo.com/v1/forecast) — live",
    "fetched_at_ist": "2026-11-12 11:35:56 IST",
    "anchor_hour_ist": "2026-11-12T11:00",
    "from_cache": false,
    "status": "ok",                        // observed-window status
    "current": { "precipitation_mm": 0.0, "...": "..." },
    "observed": {
      "windows": {
        "1h":  { "accumulation_mm": 0.0, "reference_mm": 10.0,  "normalized": 0.0,   "complete": true, "status": "ok", "hours_present": 1,   "hours_expected": 1 },
        "3h":  { "accumulation_mm": 0.0, "reference_mm": 25.0,  "normalized": 0.0,   "complete": true, "status": "ok", "hours_present": 3,   "hours_expected": 3 },
        "24h": { "accumulation_mm": 0.0, "reference_mm": 80.0,  "normalized": 0.0,   "complete": true, "status": "ok", "hours_present": 24,  "hours_expected": 24 },
        "72h": { "accumulation_mm": 0.0, "reference_mm": 150.0, "normalized": 0.0,   "complete": true, "status": "ok", "hours_present": 72,  "hours_expected": 72 },
        "7d":  { "accumulation_mm": 0.4, "reference_mm": 250.0, "normalized": 0.0016,"complete": true, "status": "ok", "hours_present": 168, "hours_expected": 168 }
      },
      "normalized_indicator": 0.0016,
      "indicator_basis": "max over complete windows of N = A / (A + R); incomplete windows excluded",
      "complete_windows": ["1h","3h","24h","72h","7d"],
      "partial_windows": [],
      "unavailable_windows": [],
      "status": "ok"
    },
    "forecast": { "kind": "forecast (next 1h/3h/24h from hourly forecast)", "windows": { "1h": {}, "3h": {}, "24h": {} }, "normalized_indicator": 0.0 },
    "missing_data_policy": "Missing hours are NEVER replaced with 0. ...",
    "reference_note": "Reference values (1h 10 mm, ...) are ILLUSTRATIVE and UNCALIBRATED defaults ..."
  },
  "combined": {
    "formula": "S = clamp01( P_terrain * (1 + alpha * N_rain) ), bounded 0-1 for display",
    "alpha": 1.0,
    "alpha_note": "alpha is UNCALIBRATED (default 1.0 or RAIN_ALPHA env / ?alpha= query); NOT tuned on any dataset",
    "rainfall_basis": "observed",
    "terrain_score": 0.9984,
    "rainfall_normalized": 0.0016,
    "combined_screening_index": 1.0,
    "uncalibrated": true,
    "bounded_0_1": true,
    "interpretation": "Screening index, NOT a calibrated landslide probability. ..."
  },
  "disclaimer": [
    "EXPERIMENTAL RAINFALL-ENHANCED SCREENING INDEX — UNCALIBRATED.",
    "The underlying RF v3 model estimates terrain susceptibility only.",
    "Rainfall is incorporated separately using an illustrative normalization and combination formula.",
    "This is NOT a calibrated landslide probability, trained rainfall-aware model, or operational warning."
  ],
  "rainfall_is_rf_predictor": false,
  "no_fabricated_predictions": true
}
```

(The snapshot above is a **real** response captured on 2026-11-12; numbers will differ as weather changes.)

**Caching:** Open-Meteo responses are cached in-process for **300 s**, keyed by `(lat, lon)` rounded to
2 decimal places (≈1.1 km). This prevents a click-storm from hammering a third-party API; `from_cache`
tells the client whether a live call was made.

---

## 7. Worked examples

### 7.1 Real live example (2026-11-12, Chamoli hotspot)

```
Point:        30.30484 N, 79.37097 E   (RF v3 terrain score 0.9984 → HIGH)
P_terrain   = 0.9984
Observed:     1h 0.0 mm, 3h 0.0 mm, 24h 0.0 mm, 72h 0.0 mm, 7d 0.4 mm
N_k         = 0.0, 0.0, 0.0, 0.0,        0.4/(0.4+250) = 0.0016
N_rain      = max(0, 0, 0, 0, 0.0016) = 0.0016
S           = 0.9984 × (1 + 1.0 × 0.0016) = 0.99999…  → reported 1.0 (4 dp; display bound)
```

Interpretation: dry weather; the combined index is essentially the terrain score alone. The 1.0 is
**saturation of a terrain score already at 0.9984**, not a rainfall effect.

### 7.2 Hypothetical moderate rain (same terrain, clearly hypothetical)

```
P_terrain   = 0.65
A_24h       = 40 mm      → N_24h = 40/(40+80) = 0.3333
N_rain      = 0.3333     (assume no other window dominates)
S           = 0.65 × (1 + 1.0 × 0.3333) = 0.8667
```

### 7.3 Sensitivity table for `P_terrain = 0.65`, `α = 1.0` (24 h window, `R_24h = 80 mm`)

| `A_24h` (mm) | `N_24h` | `S` |
|---|---|---|
| 0 | 0.0000 | 0.6500 |
| 10 | 0.1111 | 0.7222 |
| 40 | 0.3333 | 0.8667 |
| 80 | 0.5000 | 0.9750 |
| 200 | 0.7143 | 1.0000 (would be 1.1143 → clamped) |

Monotonic in rainfall for fixed terrain — one of the required tests.

### 7.4 Fetch failure / missing hours

```
Observed 24h window has 6 of 24 hours null   → status "partial", accumulation shown but window
                                               EXCLUDED from N_rain
All windows incomplete / API error           → N_rain = null, S = null,
                                               status "screening_rainfall_unavailable",
                                               combined.reason explains why
Rainfall is NEVER substituted with 0.
```

---

## 8. Frontend

Both `public/index.html` (dashboard) and `public/map.html` (map page) contain a
**“Rainfall-Normalized Screening”** panel fed by `GET /screening`:

- **Terrain score (RF v3)** with its own bar and class — explicitly labelled as unchanged terrain-only output.
- **Observed rainfall**: 1 h / 3 h / 24 h / 72 h / 7 d accumulations in mm, each next to its reference
  (`R_k`), each showing `N_k`; incomplete windows marked `⚠ partial (n/m h)`; unavailable shown as
  `unavailable (no data)` — never as 0.
- **Normalized rainfall indicator** `N_rain` (0–1) with bar and the list of excluded/unavailable windows.
- **Forecast rainfall** block, visually distinct (`FORECAST` badge) with its own `N_rain` — never mixed
  with the observed block.
- **Combined screening signal** `S` (0–1) with bar, the live `α` value, and the literal formula.
- **Legend**: green/yellow/orange/red = low/moderate/elevated/high; note that the terrain bar is the
  raw RF v3 probability.
- **Source/status line**: Open-Meteo, fetch time (IST), anchor hour, live vs 5-minute cache.
- **Mandated disclaimer, verbatim**, in a red callout.

Panels refresh on every map click and on initial page load; server-side cache keeps that to one
Open-Meteo call per ~5 minutes per location.

---

## 9. Tests

`python tests/test_rainfall_screening.py` — **12/12 pass** (custom runner, no pytest needed).

| # | Test | Requirement covered |
|---|---|---|
| 1 | `test_zero_rainfall` | zero rain, complete data ⇒ N = 0, S == terrain exactly |
| 2 | `test_monotonic_normalized` | increasing rainfall ⇒ increasing N; N < 1; N = 0.5 at A = R |
| 3 | `test_combined_increases` | increasing rainfall ⇒ increasing S for fixed terrain |
| 4 | `test_terrain_score_unchanged` | terrain input never mutated; α = 0 ⇒ S == terrain |
| 5 | `test_missing_is_unavailable_not_zero` | missing hours flagged; no complete window ⇒ `null` + `unavailable`; never zero-filled |
| 6 | `test_bounds` | N ∈ [0,1), S ∈ [0,1] across extreme inputs |
| 7 | `test_forecast_vs_observed` | observed excludes future rain, forecast sees it; window sets differ |
| 8 | `test_alpha_config` | α default/override/clamp; formula literal verified |
| 9 | `test_reference_values` | documented defaults + `RAIN_REF_*` env override |
| 10 | `test_live_api_integration` | real Open-Meteo call returns bounded indicator (skips cleanly offline) |
| 11 | `test_api_contract_matches_frontend` | in-process server; all JSON keys the JS reads exist; disclaimer present |
| 12 | `test_frontend_html_wiring` | both HTML files contain panel, disclaimer, `/screening` fetch, and every `scr*` id referenced by JS is defined |

Additional manual HTTP verification (2026-11-12): `/screening` at the hotspot, out-of-coverage point
(Delhi → `screening_terrain_out_of_coverage`, `S = null`), `?basis=forecast&alpha=2.0`, `?alpha=0`
(S == terrain), `/rf/susceptibility` unchanged, `/health` advertises the new endpoint.

---

## 10. Remaining limitations (must be shown with the feature)

1. **Uncalibrated α and reference rainfall.** Both are illustrative defaults, not fitted or validated.
2. **No rainfall-aware model.** `S` is an arithmetic combination of two separate quantities; it has no
   learned rainfall–landslide relationship and no event data to support one.
3. **Terrain model is single-event.** RF v3 was trained on the 1999 earthquake event in Chamoli; its
   ROC-AUC is 0.700 (95 % CI 0.596–0.808) and PR-AUC 0.091. Rainfall cannot repair that.
4. **Saturating formula.** With HIGH terrain scores, `S` pins at 1.0 for very light rain — the index
   discriminates best in low-to-moderate terrain scores. This is a property of the chosen formula,
   not evidence of high hazard.
5. **Point rainfall, not areal.** Open-Meteo gives a grid-cell estimate at the clicked coordinate;
   it is not a rain gauge and not a radar/IMERG analysis.
6. **Observed/forecast are distinct inputs.** Default `S` uses observed rainfall; the forecast basis is
   opt-in and must be labelled whenever shown.
7. **Forecast horizon limited** to 1 h/3 h/24 h (matching the Open-Meteo hourly product used).
8. **Cache staleness up to 5 minutes** by design.
9. **Not an operational warning.** No threshold of `S` has been validated for alerting; none is offered.

---

## 11. Files

| File | Change |
|---|---|
| `api/screening_utils.py` | **new** — formulas, references, cache, window/missing-data logic, disclaimer |
| `api/server.py` | `_terrain_inference` shared by old + new handlers; `_handle_screening_request`, `_handle_screening`; `/screening` route; `/health` advertises it |
| `public/index.html` | new screening panel, CSS, `fetchScreening()`, legend, disclaimer; stale v1 metrics strings corrected |
| `public/map.html` | screening block in RF panel, CSS, `fetchScreening()`, legend, disclaimer; stale v1 metrics strings corrected |
| `tests/test_rainfall_screening.py` | **new** — 12 tests |
| `docs/RAINFALL_SCREENING.md` | **new** — this document |
| `COMPLETION_TRACKER.md`, `docs/FINAL_DELIVERY.md` | addendum for this feature |

**Not touched:** `models/rf_pixel_susceptibility_pipeline{,_v2,_v3}.joblib`, all `*_metadata*.json`,
the v3 training CSV, and all raw datasets.
