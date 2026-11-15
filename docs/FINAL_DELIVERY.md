# TerraGuardAI — Final Delivery (2026-11-11)

**Deadline: 2026-11-15 | Pilot: Chamoli District, Uttarakhand, India | Status: working application; experimental ML product.**

> ⚠️ The susceptibility product is **EXPERIMENTAL research**. It was trained on a single
> earthquake event (1999 Mw 6.6) in one district. It is not an operational hazard forecast,
> must not be used for emergency decision-making, and is labeled EXPERIMENTAL everywhere
> it appears (backend payloads, layer UI, reports).

---

## 1. What this application is

A full-stack research prototype:
- **Backend** (`api/server.py`, pure Python stdlib + rasterio/joblib/pyproj for inference; port 5000) serving the map, dataset metadata, live Open-Meteo weather, click-time RF inference, and the experimental susceptibility raster/overlay.
- **Frontend** (`public/map.html`, `public/index.html`) — the 2026-11-11 sprint pass left these to Antigravity (see `docs/ANTIGRAVITY_HANDOFF.md`); the **2026-11-12 rainfall-screening feature** was implemented directly in both files (see §12 and `docs/RAINFALL_SCREENING.md`).
- **ML**: Random Forest susceptibility model trained **only** on SRTM DEM derivatives (elevation, slope, aspect-sin/cos, hillshade). Rainfall is *never* an RF predictor; rainfall is monitored independently.

## 2. Architecture

```
SRTM DEM (n30_e078/n30_e079/n31_e079, 1-arc)
        │  merge + reproject (terrain_corrected/*_prj.tif, ~28.7 m, EPSG:32644)
        ▼
pixel_rf_dataset_elevation_terrain_complete.csv   ← 49 landslide polygons (1 px each)
        │                                               + 4,995 background (bbox+1km, 50 m buffer, seed 42)
        ▼
spatial 5-band x-band nested CV (band = x in UTM) — selection AND threshold tuning inside training folds
        ▼
RF v3 selected (district AOI)  ── frozen threshold T=0.38 (pooled training OOF F1)
        ▼
 susceptibility_chamoli_experimental.tif (+ 4326 copy, PNG overlay, overlay.json)
        ▼
api/server.py : /rf/susceptibility (click), /susceptibility/raster|overlay.png|overlay.json|metadata, /inventory, /weather, /datasets, /layers
        ▼
public/map.html — Leaflet map (Esri satellite/topo, OSM), weather dashboard, inventory layer (candidate), RF layer (candidate, wire in Antigravity)
```

## 3. Run it

```powershell
cd D:\TerraGuardAI
# RF inference needs the project venv (rasterio + joblib + pyproj):
python api\server.py
# Pages:  http://localhost:5000/index.html    http://localhost:5000/map.html
# API:    http://localhost:5000/              /datasets  /layers  /inventory
#         http://localhost:5000/weather?lat=30.33&lon=79.42
#         http://localhost:5000/rf/susceptibility?lat=30.33&lon=79.42
#         http://localhost:5000/susceptibility/raster  /overlay.png  /overlay.json  /metadata
```

Rebuild the experimental susceptibility raster (only if model/DEM change):
`python phaseC_build_susceptibility_raster.py` (requires rasterio, joblib, numpy, geopandas, matplotlib).

## 4. Model selection summary (all numbers are from preserved reports)

| Model | AOI | Spatial-CV selection metric | Test (untouched) | Decision |
|---|---|---|---|---|
| RF v1 | district | — | ROC-AUC 0.596 | superseded |
| RF v2 (T=0.27) | district | — | P 0.167 / R 0.040 / ROC-AUC 0.719 | superseded |
| **RF v3 (T=0.38)** | district | PR-AUC **0.252** / ROC-AUC 0.857 | P 0.111 / R 0.080 / F1 0.093 / **ROC-AUC 0.700 [0.596–0.808]** / PR-AUC 0.091 [0.024–0.204] | **SELECTED** |
| RF v4 (T=0.39) | expanded, 73 polys | PR-AUC 0.135 / ROC-AUC 0.765 | P 0.059 / R 0.056 / F1 0.057 / ROC-AUC 0.739 (descriptive) | **Rejected** (AOI redefinition not justified; worse on frozen rule) |

The expanded-AOI dataset (`pixel_rf_dataset_expanded_73.csv`) and its metrics are preserved
as a documented experiment (`reports/rf_v4_experiments.*`, `reports/rf_v4_final_evaluation.json`).

## 5. Experimental susceptibility raster — what it contains (honest stats)

- AOI: bbox of the 49 district polygons + 2 km (a bounding box, **not** the district polygon).
- 2,515,410 valid pixels; probability min 0.0 / max 0.998 / **mean 0.0176 / median 0.0**; p90 0.029; p99 0.425; 1.2% of pixels ≥ 0.38.
- In-sample descriptive (training polygons influenced fitting — not an evaluation metric):
  mean probability inside the 49 polygon interiors **0.148** vs **0.018** elsewhere; 17.4% of interior pixels ≥ 0.38.
- Overlay ramp is absolute 0–1 (not stretched); nodata is transparent.
- Feature importances (v3): slope 0.344, aspect_cos 0.255, elevation 0.158, hillshade 0.136, aspect_sin 0.107.

## 6. Verification evidence (HTTP-level, 2026-11-11)

- Server starts; `/`, `/health`, `/datasets`, `/layers` → 200; `/weather?lat=30.33&lon=79.42` → live Open-Meteo JSON (precip 0.0, timezone Asia/Kolkata, 168 h + 7 d).
- `/inventory`, `/susceptibility/overlay.png`, `/overlay.json`, `/raster`, `/metadata` → 200; served bytes **md5-identical** to disk; raster served as attachment `susceptibility_chamoli_experimental.tif`.
- `/rf/susceptibility`:
  - In-AOI max-probability pixel (30.30484N, 79.37097E) → 0.9984, class HIGH — matches the GeoTIFF exactly.
  - In-AOI (30.33N, 79.42E) → 0.0 LOW; (30.42N, 79.18E) → 0.0098 LOW — matches raster pixels.
  - Delhi/ocean clicks → `model_trained_out_of_coverage`, missing terrain listed, **no fabricated prediction**.
- NoData: raster nodata=-9999; API treats elevation nodata (-32767) / NaN features / out-of-bounds as "missing" → honest out-of-coverage response.

**Not verified (blocked):** browser console, screenshot capture, responsive layout — no browser
automation (selenium/playwright missing; pip blocked). Frontend acceptance must be completed in Antigravity.

## 7. Phase D — frontend

1. Wire the RF layer from `/susceptibility/overlay.png` + `overlay.json` (see `docs/ANTIGRAVITY_HANDOFF.md` §3) with opacity control + EXPERIMENTAL legend.
2. Wire inventory polygons from `/inventory` (EPSG:4326, 49 features).
3. Replace stale v1 strings (ROC-AUC 0.596 → v3, file paths, IMERG status, terrain path, "RF layer not wired") per handoff §5. — **partially done on 2026-11-12**: the stale ROC-AUC/model-file strings were corrected in both HTML files while adding the rainfall-screening panel.
4. Verify in browser: console clean, layers load, mobile layout, attribution. — **still pending** (no browser automation in this environment).

## 7b. Rainfall-screening frontend (done 2026-11-12)

Both `public/index.html` and `public/map.html` now contain a **Rainfall-Normalized Screening** panel
(terrain score, 1 h/3 h/24 h/72 h/7 d observed accumulations, forecast block, `N_rain`, combined `S`,
references, α, legend, source/observed-vs-forecast status, mandated disclaimer). Full method:
`docs/RAINFALL_SCREENING.md`.

## 8. Known limitations (do not remove from UI/docs)

1. Single event type (earthquake-triggered, 1999 Mw 6.6); no rainfall-triggered model exists.
2. One district; 49 positives; ~100:1 imbalance; spatially correlated data.
3. Metrics are weak in absolute terms (PR-AUC 0.091); the model is a demo, not a forecast.
4. RF probabilities are uncalibrated vote shares.
5. AOI = inventory bbox (+2 km), not a legal district boundary.
6. Geological/lithological predictors: **none acquired** (network blocked this session).
7. No operational-reliability claim is made anywhere in this project.
8. **Rainfall screening is uncalibrated**: reference rainfall (10/25/80/150/250 mm for 1 h/3 h/24 h/72 h/7 d) and α (default 1.0) are illustrative, not fitted; `S` is an arithmetic combination, not a trained rainfall-aware prediction, and not a calibrated probability. See `docs/RAINFALL_SCREENING.md` §10.

## 9. Final demonstration checklist (for the 2026-11-14/15 review)

- [ ] `python api/server.py` starts; open `http://localhost:5000/` and `/map.html`
- [ ] Click map in Chamoli ~ (30.33, 79.42): weather card populates; RF card shows LOW/⚡EXPERIMENTAL
- [ ] Click (30.30484, 79.37097): RF card shows HIGH (0.9984)
- [ ] Click Delhi: out-of-coverage message, no prediction
- [ ] Toggle RF layer: orange/red hotspots over satellite, opacity slider works, legend labeled EXPERIMENTAL
- [ ] Inventory layer: 49 red polygons; popup shows Lithology/Trigger/Near_to
- [ ] Datasets section reflects v3 model + IMERG present
- [ ] Rainfall card and RF card visibly separated; EXPERIMENTAL label visible
- [ ] Rainfall-Normalized Screening panel: terrain score, 5 observed windows with references, forecast block, `N_rain`, `S`, α, legend, source line, mandated disclaimer
- [ ] `/screening?lat=30.30484&lon=79.37097` returns `screening_complete`; Delhi returns `screening_terrain_out_of_coverage` with `S = null`
- [ ] `python tests/test_rainfall_screening.py` → 12/12 pass
- [ ] `/susceptibility/raster` downloads the GeoTIFF; GIS opens it (EPSG:32644)
- [ ] Console has no errors; mobile view usable
- [ ] This document + `docs/ANTIGRAVITY_HANDOFF.md` + `COMPLETION_TRACKER.md` in final zip

## 10. Blockers (honest, as of 2026-11-11)

| Blocker | Impact | Resolution needed |
|---|---|---|
| No network egress (Overpass 502/timeouts; DNS failures; pip blocked; web search cancelled) | GSI geology, OSM roads/rivers, external inventories unverifiable | Access or manual download of GSI/NRSC layers |
| No browser automation (selenium/playwright missing, pip blocked) | Phase E limited to HTTP tests; no screenshots | Run frontend verification in Antigravity |
| Frontend owned by Antigravity | RF layer/legend/stale stats not yet wired | Handoff doc delivered; Antigravity pass required |
| AOI is a bbox (no district boundary shapefile in repo) | Raster mask is rectangular + DEM validity, not district outline | Add Chamoli district boundary GeoJSON if available |

## 11. Files created/changed in this sprint

Created: `phaseA_audit_excluded_polygons.py`, `phaseB_build_expanded_dataset.py`, `phaseB_v4_experiments.py`, `phaseB_v4_final_evaluation.py`, `phaseC_build_susceptibility_raster.py`, `data/processed/pixel_rf_dataset_expanded_73.csv(+_manifest.json)`, `data/processed/inventory_chamoli_epsg4326.geojson`, `data/processed/susceptibility_chamoli_experimental{.tif,_4326.tif,_overlay.png,_overlay.json}`, `reports/phaseA_polygon_audit.{txt,json}`, `reports/rf_v4_experiments.{json,txt}`, `reports/rf_v4_final_evaluation.json`, `reports/rf_v3_susceptibility_raster_report.{txt,json}`, `docs/ANTIGRAVITY_HANDOFF.md`, `docs/DATASET_INVENTORY.md`, `docs/FINAL_DELIVERY.md`, `COMPLETION_TRACKER.md`.

Changed: `api/server.py` (v3 model wiring, `/inventory`, `/susceptibility/*`, `/health`, `/datasets`, new helpers; `/weather` untouched).

**2026-11-12 rainfall-screening feature — created:** `api/screening_utils.py`, `tests/test_rainfall_screening.py`, `docs/RAINFALL_SCREENING.md`.
**Changed:** `api/server.py` (`_terrain_inference`, `_handle_screening_request`, `_handle_screening`, `/screening`, `/health`), `public/index.html`, `public/map.html` (screening panel + `fetchScreening()` + legend + disclaimer; stale v1 metric strings corrected), `COMPLETION_TRACKER.md`, this file.

**Not touched:** v1/v2/v3 models and their metadata, all original raw datasets, the v3 training CSV, all other `docs/*.md`.

---

## 12. Rainfall-normalized screening signal (added 2026-11-12)

**Feature:** the frozen RF v3 terrain score and live Open-Meteo rainfall are combined into a single
**experimental screening index** `S`, with the terrain score kept separate and unchanged.
Method, exact formulas, references, worked examples and tests: **`docs/RAINFALL_SCREENING.md`**.

**Exact formulas**

```
N_k    = A_k / (A_k + R_k)                      per-window normalized rainfall, in [0,1), = 0.5 at A=R
N_rain = max(N_k over COMPLETE windows)         null if no window is complete
S      = clamp01( P_terrain × (1 + α × N_rain) ) α default 1.0, illustrative & UNCALIBRATED
```

**Reference rainfall (illustrative, UNCALIBRATED, env-overridable):**
1 h **10 mm**, 3 h **25 mm**, 24 h **80 mm**, 72 h **150 mm**, 7 d **250 mm**.
These are plausible magnitudes, **not** Chamoli landslide thresholds and **not** validated against any
inventory. Missing hours are **never** zero-filled: incomplete windows are flagged and excluded; with no
complete window, `N_rain = null`, `S = null`, status `screening_rainfall_unavailable`.

**Endpoint:** `GET /screening?lat=&lon=&alpha=&basis=observed|forecast`
(observed = trailing 1/3/24/72/168 h ending at the last complete hour; forecast = next 1/3/24 h;
default basis = observed; 300 s cache per ~1.1 km cell; `/rf/susceptibility` unchanged).

**Worked example (real, 2026-11-12):** hotspot 30.30484 N, 79.37097 E — `P_terrain = 0.9984`;
observed 1 h/3 h/24 h/72 h = 0 mm, 7 d = 0.4 mm → `N_rain = 0.4/250.4 = 0.0016`;
`S = 0.9984 × 1.0016 = 0.99999… → 1.0` (4 dp; saturation of an already-HIGH terrain score).
Hypothetical: `P = 0.65`, 24 h = 40 mm → `N = 0.3333` → `S = 0.8667`.

**Tests:** `python tests/test_rainfall_screening.py` → **12/12 pass** (zero rain, monotonicity,
combined monotonicity, terrain unchanged, missing → unavailable not zero, bounds, forecast vs observed,
α config, reference overrides, live API, API/frontend contract, frontend wiring).

**Frontend:** both `public/index.html` and `public/map.html` display terrain score, all observed
accumulations with references, the normalized indicator, the forecast block (labelleled FORECAST),
the combined index, α, the legend, the rainfall source/status, and the **mandated disclaimer verbatim**:
*"EXPERIMENTAL RAINFALL-ENHANCED SCREENING INDEX — UNCALIBRATED. The underlying RF v3 model estimates
terrain susceptibility only. Rainfall is incorporated separately using an illustrative normalization and
combination formula. This is NOT a calibrated landslide probability, trained rainfall-aware model, or
operational warning."*

**Guarantees:** RF v3 artifact untouched (SHA-256 `CC81FA0A…E742FBCB`, mtime 2026-11-11 21:36:50);
rainfall is **not** an RF predictor (`rainfall_is_rf_predictor: false`); no fabricated predictions.

---

# §13. Map-first dashboard delivery update (2026-11-12)

This section supersedes §12's note that browser verification was blocked. The RF v3 model, its frozen
threshold, `/rf/susceptibility`, `/screening`, `/inventory`, `/susceptibility/*`, the datasets, and the
rainfall normalization formulas are **unchanged**; this update is presentation, routing, and an additive
weather extension.

## 13.1 What changed

| Area | Change |
|---|---|
| Routing | `/` now serves the dashboard HTML. `/health` and `/status` still return JSON. Before this fix, `/` returned health JSON, so the app only worked at `/map.html`. |
| Shell | `public/index.html` is canonical. `public/map.html` is **generated** from it by `scripts/build_frontend.py` (only `<body data-page="map">` and `<title>` differ). |
| Assets | All CSS in `public/assets/terraguard.css`; all logic in `public/assets/terraguard.js` (no inline handlers, no duplicate listeners). |
| Basemaps | Esri World Imagery (satellite), Esri World Topographic, Esri World Street, OpenStreetMap. Custom radio list, not `L.control.layers`. Attribution bottom-right; zoom + scale bottom-left above the status bar. |
| Extent | Initial fit to the RF v3 / inventory AOI `[[30.10,78.90],[30.52,79.45]]`, padded for the side panels. |
| Weather API | `/weather` extended additively: temperature, apparent temperature, humidity, wind speed/direction, gusts, precipitation, WMO condition, cloud cover, pressure, sunrise/sunset, daily min/max; all pre-existing keys retained. 300 s in-process cache keyed on lat/lon (2 dp). |
| Default point | Chamoli town `30.40, 79.32` (inside AOI), so weather/screening work before any GPS request. |
| UI | Dark navy/charcoal translucent panels, top nav (logo, Dashboard/Map, study-area chip, My Location, fullscreen), left layers panel, right weather + screening, bottom status bar, collapsible panels, mobile slide-in sheets at ≤980 px. |
| Susceptibility | Toggle, opacity slider (default 40%), legend, exact label `EXPERIMENTAL TERRAIN SUSCEPTIBILITY`, "uncalibrated, not a probability" caveat. |
| Inventory | Toggle + opacity + genuine attribute popups for the 49 `/inventory` polygons. |
| My Location | `navigator.geolocation.getCurrentPosition` **only** from the click handler; flyTo, pulsing marker, accuracy circle, explicit denial/timeout/unavailable handling; never hardcoded; no prompt on load. |
| Screening panel | Combined index first, then RF v3 terrain score, observed 1/3/24/72 h + 7 d with references, `N_rain`, separate forecast block, α, legend, source/status, model caveat, mandated disclaimer verbatim. |

## 13.2 Run it

```
cd D:\TerraGuardAI
python api\server.py                 # http://localhost:5000  (serves the app at /)
python scripts\build_frontend.py     # regenerate public\map.html after editing index.html
```

Optional checks:

```
python tests\test_ui_dashboard.py      # 11 route / asset / contract / wiring tests
python tests\test_rainfall_screening.py  # 12 rainfall + API + frontend tests
node scripts\verify_ui.js http://localhost:5000   # 53 real-browser checks (launches headless Edge)
```

The browser harness needs Node ≥ 22 (built-in `WebSocket`/`fetch`) and Microsoft Edge; it does not use
npm packages. Screenshots and `results.json` are written to `%TEMP%\terraguard-ui-verify\`.

## 13.3 Verification evidence (2026-11-12)

- **Browser (real Edge 156, DevTools Protocol): 53/53 checks pass.** Covered: dashboard load at `/`,
  live `/weather` contract (22 °C, feels 21 °C, 31 % RH, 2 km/h wind, 0.0 mm, gusts 16 km/h,
  12 hourly tiles, 7 daily rows, last-update stamp), screening values (`P_terrain 0.0 % LOW`,
  `N_rain 0.0016`, `S 0.0000`), RF v3 point query, 49 inventory features, overlay toggle + opacity
  (`0.25` / "25%"), basemap switch (Esri Topographic tiles + status label), weather tabs, panel
  collapse/expand, fullscreen enter/exit, map-click re-query (weather + screening + RF refresh),
  blocked-`/weather` error + Retry recovery, geolocation success with a synthetic GPS fix
  (`Emulation.setGeolocationOverride` + permission grant), mobile 390×844 (no horizontal overflow,
  sheets inside viewport, weather card not internally clipped), `/map.html` variant, and
  **zero uncaught JS errors**.
- **Python: 11/11** (`tests/test_ui_dashboard.py`) and **12/12** (`tests/test_rainfall_screening.py`).
- **Live data at default point 30.40 N, 79.32 E:** terrain 0.0 % LOW; observed 1 h/3 h/24 h/72 h
  0.0 mm, 7 d 0.4 mm → `N_rain 0.0016`; `S 0.0000`. Weather 21.8–22 °C, Mainly clear.
- **Screenshots** inspected at 1600×1000 and 390×844 (desktop, mobile layers sheet, mobile
  weather/screening sheet).

## 13.4 Honest limitations

1. Basemaps are **static tile services**. The UI states this explicitly ("Static Esri / OSM basemap
   tiles — not live imagery"). Nothing in the app claims live satellite imagery.
2. The susceptibility overlay is an **uncalibrated, absolute-probability raster** from a model trained
   on 49 earthquake-triggered polygons from one 1999 event in one district. It is not a probability
   estimate for operational use, and the legend says so.
3. The screening index is **illustrative** (`S = clamp₀₁(P_terrain × (1 + α·N_rain))`, α = 1.0,
   references 10/25/80/150/250 mm). Rainfall is not an RF predictor (`rainfall_is_rf_predictor: false`).
4. Weather is an Open-Meteo **model point forecast**, not a station observation, and the hourly/daily
   blocks are forecasts (labelled). No rainfall station data are ingested.
5. Terrain inference is only valid inside the AOI; outside it the API returns
   `model_trained_out_of_coverage` and the panel shows that status rather than inventing a score.
6. Fullscreen and geolocation were verified in headless Edge via trusted input events / CDP overrides;
   behaviour in other browsers is expected but not browser-matrix tested.
7. General network egress is restricted in this environment: Esri tiles and Open-Meteo work; bulk
   Overpass/GSI/NRSC acquisition does not.

## 13.5 Manual test steps (≈3 minutes)

1. `python api\server.py`, open `http://localhost:5000/`. Confirm the dashboard (not JSON) loads over
   Chamoli with satellite imagery and the susceptibility overlay at 40 %.
2. Confirm weather shows current values + "updated HH:MM:SS IST", and that **Hourly** / **7-day** tabs
   switch the forecast block. No GPS prompt appears on load.
3. Click a point on the map (~300 ms after release). Confirm the place label changes to "Selected point",
   the marker moves, and weather + screening + RF terrain refresh.
4. Toggle susceptibility off/on; drag opacity to 25 % and confirm the legend/modern overlay respond.
   Toggle inventory and click a polygon to read its attributes.
5. Switch basemaps (Imagery → Topographic → Street → OSM); confirm the status bar label and tiles change.
6. Click **My Location**; allow permission. Confirm flyTo, pulsing marker + accuracy radius, coordinates,
   "My location (±N m)", and weather for that point. Deny permission on a second run and confirm the
   toast explains the denial.
7. Collapse the left and right panels; click **Fullscreen**; press Esc.
8. Resize to ~390 px (or use device emulation). Confirm the mobile bar, slide-in sheets, scrim, no
   horizontal scrollbar, and that the weather card shows the full hourly strip.
9. Open `http://localhost:5000/map.html`; confirm it is the same app with the map page active.
10. Confirm the screening panel keeps: combined index, RF v3 terrain score, observed windows with
    references, `N_rain`, separate forecast, α, source, and the mandated disclaimer verbatim.

# TerraGuardAI - Section 14 addendum (2026-11-12): Susceptibility visualization & interpretation upgrade

## 14. Susceptibility visualization & interpretation upgrade (2026-11-12)

Implements the requested upgrade on top of the delivered dashboard (no redesign; DRISHTI-inspired dark
map-first interface preserved).

### 14.1 Changes

1. **Clearly visible susceptibility heatmap**
   - The overlay raster (`data/processed/susceptibility_chamoli_experimental_overlay.png`) was regenerated
     from the source RF v3 probability raster with a **continuous Green -> Yellow -> Orange -> Red gradient**
     over the absolute probability (0..1, NOT stretched; NOT alpha-blended per-pixel scaling); nodata /
     outside-extent pixels are fully transparent. The overlay JSON `mode` field was updated accordingly.
   - The left-panel legend gradient bar was changed from the previous yellow->red ramp to the same
     Green->Yellow->Orange->Red gradient; tick bands updated to LOW < 0.15, MODERATE 0.15-0.38, HIGH >= 0.38
     (frozen threshold 0.38).
   - Overlay toggle, 0-100% opacity slider (default 40%), and bounds from `/susceptibility/overlay.json`
     are unchanged.

2. **Prominent location assessment**
   - On map click (and via geolocation-driven selection), the selected-point popup now prominently shows:
     terrain susceptibility category (LOW / MEDIUM / HIGH), the exact RF v3 model score (4 dp),
     selected latitude/longitude, a green->red gradient bar with the score marker, a concise
     interpretation of the result, and the disclaimer
     "Experimental terrain susceptibility assessment; not a landslide prediction or safety determination.".
   - Display-only category banding was added: HIGH = score >= frozen threshold (0.38); MEDIUM = score in the
     0.15-0.38 experimental band; LOW = score < 0.15. The backend `/rf/susceptibility` classification
     (LOW/HIGH) was intentionally left unchanged; no scores were fabricated.

3. **Scientific accuracy / no confusion**
   - Categories remain experimental relative model scores; the interpretation text never calls LOW safe or
     HIGH an imminent warning; required disclaimer is displayed.
   - Inventory polygons were restyled from orange to blue so they are visually distinct from the
     susceptibility gradient (red/orange) and not confused with the susceptibility visualization; the
     inventory popup already notes the 1999 Mw 6.6 event context.

### 14.2 Verification (this session)

- Browser: `node scripts/verify_ui.js http://localhost:5000` -> **59/59 checks pass** (was 53/53 before the
  susceptibility upgrade; +6 new checks: overlay bounds, hotspot HIGH + exact score + disclaimer, low LOW,
  out-of-AOI, legend gradient/disclaimer).
- Python: `python tests/test_ui_dashboard.py` -> **11/11 pass**; `python tests/test_rainfall_screening.py`
  -> **12/12 pass** (overlay mode gradient assertions and terrain-assessment disclaimer wiring added).
- Overlay raster regenerated from source RF v3 probability raster; gradient verified (green at low score,
  red at high score), alpha=0 transparency preserved for nodata pixels (46,620 px) and outside-extent pixels.

### 14.3 Limitations

- The LOW/MEDIUM/HIGH bands are display-level experimental interpretations; the authoritative model class
  remains LOW/HIGH from the frozen threshold (0.38); backend behaviour unchanged.
- Overlay regeneration samples the source probability raster onto the published overlay grid; the gradient is
  over absolute probability and is not stretched.

### 14.4 Manual test steps (susceptibility)

1. `python api\server.py` -> open `http://localhost:5000/`.
2. Toggle the terrain susceptibility overlay; confirm green->yellow->orange->red rendering over satellite;
   drag opacity.
3. Click a point inside the AOI with high model score (e.g. ~30.30 N, 79.37 E) -> confirm popup category
   HIGH, exact score (e.g. 0.9984), lat/lon, gradient bar marker, interpretation, disclaimer.
4. Click the default point (30.40 N, 79.32) -> confirm category LOW, score 0.0000.
5. Click far outside the AOI (e.g. ~19.5 N, 70.5 E) -> confirm honest `outside AOI` handling, no score.
6. Confirm inventory polygons (blue) are visually separate from the susceptibility heat overlay.
7. Re-run the browser harness (`node scripts/verify_ui.js http://localhost:5000`) -> expect 59/59 pass.

# TerraGuardAI - Section 15 addendum (2026-11-12): Susceptibility spatial consistency audit & correction

Implements the requested **susceptibility spatial consistency audit & correction**: verify that RF v3 point
inference, the susceptibility raster, the heatmap colours and the displayed location assessments agree
spatially and numerically, and correct the root cause if an inconsistency is found.

## 15. Susceptibility spatial consistency audit & correction (2026-11-12)

### 15.1 Audit scope

Inspected: `api/server.py` (RF v3 `_terrain_inference`, `_sample_raster`, `_sample_raster_xy`), the
`/rf/susceptibility` and `/susceptibility/*` endpoints, the raster generation logic, the frontend overlay and
assessment wiring.

Coordinate chain: click (lat, lon) -> EPSG:4326 -> EPSG:32644 (UTM) -> slope/aspect/hillshade sampled from
predictor rasters at UTM pixel -> DEM elevation sampled -> RF v3 -> probability.

### 15.2 Root cause of the point-vs-raster discrepancy

The susceptibility raster was generated by running the frozen RF v3 over a UTM raster pixel grid using
**pixel-center** feature sampling. The click-time inference in `_terrain_inference` sampled the DEM
**elevation at the raw clicked lat/lon**, which can resolve a different (coarser-grid) DEM pixel than the
raster pixel containing the click. The resulting elevation difference (~14 m observed) fed different features
into the frozen RF v3 and produced a different probability.

Before correction, at (30.30069 N, 79.37200 E) the click-time RF v3 probability was 0.0685 while the containing
raster pixel held 0.0387 -> discrepancy -0.0298.

### 15.3 Correction

Added `Handler._sample_elevation(...)` in `api/server.py`: when the click is known in UTM metres, the DEM
elevation is read at the **center** of the susceptibility raster pixel containing the click (UTM -> lat/lon
via inverse transform -> DEM sample). Click-time inference now reproduces the raster-grid prediction.

The frozen RF v3 model, artifacts, features, weights and threshold (0.38) were NOT modified; rainfall remains
not an RF predictor.

### 15.4 Exact point/raster scores after correction (within 4-dp display rounding)

| Coordinate (lat, lon) | RF v3 point (display) | Raster pixel value | Diff | Class |
|---|---|---|---|---|
| Hotspot (30.30484, 79.37097) | 0.9984 | 0.9983962 | -0.000004 | HIGH |
| (30.30069, 79.37200) | 0.0387 | 0.038678 | -0.000022 | LOW |
| (30.35850, 79.35938) | 0.0 | 0.0 | 0.0 | LOW |
| (30.40000, 79.32000) | 0.0 | 0.0 | 0.0 | LOW |
| (30.27137, 79.20692) | 0.8701 | 0.8701244 | +0.000024 | HIGH |

Six randomly selected valid AOI locations were also tested after the fix; all matched the containing raster
pixel within display rounding (max diff 4.8e-5). Out-of-coverage click (19.5, 70.5) -> honest
`model_trained_out_of_coverage`, no fabricated probability.

### 15.5 CRS / alignment and heatmap consistency

- Susceptibility raster: EPSG:32644 (UTM), ~28.74 m pixels; DEM EPSG:4326; overlay bounds EPSG:4326.
- Overlay: continuous Green(low) -> Yellow(0.45) -> Orange(0.75) -> Red(high) gradient over absolute probability;
  NOT stretched; nodata/outside-extent fully transparent (46,620 transparent px verified).
- Display category banding: LOW < 0.15, MEDIUM 0.15-0.38, HIGH >= 0.38 (frozen threshold); backend class
  HIGH iff prob >= 0.38 (LOW otherwise) - unchanged.

### 15.6 Files modified (2026-11-12 session)

- `api/server.py` — added `Handler._sample_elevation` and wired it into `_terrain_inference`.
- `tests/test_spatial_consistency.py` — new spatial consistency regression suite (6 tests).
- `COMPLETION_TRACKER.md`, `docs/FINAL_DELIVERY.md` — audit/fix documentation.

### 15.7 Tests executed and actual results (2026-11-12)

- `python tests/test_spatial_consistency.py` -> **6/6 pass** (point==raster at 4 coords incl. hotspot; raster
  CRS/grid + overlay bounds; overlay gradient colours + nodata; out-of-coverage honesty; LOW/MEDIUM/HIGH bands;
  /rf/susceptibility contract).
- `python tests/test_ui_dashboard.py` -> **11/11 pass** (unchanged behaviour after fix).
- `python tests/test_rainfall_screening.py` -> **12/12 pass** (rainfall/screening math untouched).
- `node --check public/assets/terraguard.js` -> OK.
- `python -m py_compile api/server.py` -> OK.
- Browser: `node scripts/verify_ui.js http://localhost:5000` -> **59/59 checks pass** (incl. overlay bounds,
  hotspot HIGH + score + disclaimer, low LOW, out-of-AOI, legend gradient).

### 15.8 Remaining scientific limitations

- The fix aligns click-time inference with the raster-grid prediction (same DEM pixel source); it does not
  retrain or alter the frozen model.
- DEM resolution vs raster grid resolution still introduce sub-pixel sampling context; differences are now
  consistent by construction with the published raster.
- RF v3 probabilities remain uncalibrated experimental outputs (ROC-AUC 0.700, PR-AUC 0.091); HIGH/LOW is a
  frozen descriptive threshold, not a hazard forecast.
- Static basemaps, headless-only browser verification, restricted network egress remain as previously documented.


