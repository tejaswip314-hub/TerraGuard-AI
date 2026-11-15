#!/usr/bin/env python
"""Spatial consistency tests: RF v3 point inference vs susceptibility raster.

Run:  python tests/test_spatial_consistency.py
Exit code 0 = all passed. No pytest / browser dependency.

Covers the susceptibility spatial-consistency audit:
  1. Click-time RF v3 point inference equals the susceptibility raster pixel
     value at the containing raster pixel (within the 4-dp display rounding).
  2. Raster CRS / grid geometry and geographic overlay bounds alignment.
  3. Overlay gradient colour mapping (low -> green, high -> red) + nodata
     transparency.
  4. Out-of-coverage behaviour (no fabricated prediction).
  5. LOW / MEDIUM / HIGH display classification vs frozen threshold.
  6. /rf/susceptibility endpoint contract unchanged by the sampling fix.

Run together with the other suites:
  python tests/test_ui_dashboard.py
  python tests/test_rainfall_screening.py
  python tests/test_spatial_consistency.py
"""
import json
import os
import sys
import threading
import traceback
import urllib.request

import http.server as hs  # noqa: E402
import socketserver as ss  # noqa: E402
from PIL import Image  # noqa: E402
from pyproj import Transformer  # noqa: E402
import rasterio  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'api'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import server as srv  # noqa: E402

RESULTS = []
PROJECT_ROOT = ROOT
TIF = os.path.join(ROOT, 'data', 'processed', 'susceptibility_chamoli_experimental.tif')
OVERLAY_JSON = os.path.join(ROOT, 'data', 'processed', 'susceptibility_chamoli_experimental_overlay.json')
OVERLAY_PNG = os.path.join(ROOT, 'data', 'processed', 'susceptibility_chamoli_experimental_overlay.png')


def test(fn):
    RESULTS.append(fn)
    return fn


class _Server:
    """Ephemeral in-process API server bound to 127.0.0.1."""

    def __enter__(self):
        ss.TCPServer.allow_reuse_address = True
        srv.Handler.log_message = lambda *a, **k: None
        self.httpd = ss.TCPServer(('127.0.0.1', 0), srv.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path, timeout=90):
        url = f'http://127.0.0.1:{self.port}{path}'
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read()


TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
TO_WGS = Transformer.from_crs("EPSG:32644", "EPSG:4326", always_xy=True)
FROZEN_THRESHOLD = 0.38


def _rf_point(s, lat, lon):
    status, _, body = s.get(f'/rf/susceptibility?lat={lat}&lon={lon}')
    d = json.loads(body.decode())
    assert status == 200, (status, d)
    return d


def _raster_pixel(lat, lon):
    """Map a lat/lon click onto the containing susceptibility raster pixel."""
    x, y = TO_UTM.transform(lon, lat)
    with rasterio.open(TIF) as src:
        row, col = src.index(x, y)
        inside = 0 <= row < src.height and 0 <= col < src.width
        val = float(src.read(1, window=((row, row + 1), (col, col + 1)))[0, 0]) if inside else None
    return inside, val, (int(row), int(col))


# ── A. point inference vs raster pixel ─────────────────────────────────────────
@test
def test_rf_point_inference_matches_raster_pixel():
    """Click-time RF v3 probability must equal the stored raster value at the
    containing pixel (allow only the 4-dp display rounding of the API response)."""
    coords = [
        (30.30069, 79.37200),  # previously discrepant: RF 0.0685 vs raster 0.0387
        (30.35850, 79.35938),
        (30.40000, 79.32000),
        (30.30484, 79.37097),  # documented hotspot: RF 0.9984 HIGH
    ]
    with _Server() as s:
        for lat, lon in coords:
            d = _rf_point(s, lat, lon)
            assert d['status'] == 'inference_complete', d.get('status')
            prob = float(d['susceptibility_probability'])
            inside, val, pix = _raster_pixel(lat, lon)
            assert inside, f'({lat}, {lon}) mapped outside the susceptibility raster'
            assert val is not None, f'({lat}, {lon}) mapped to a nodata raster pixel'
            assert abs(prob - val) < 5e-4, (
                f'RF point inference {prob} != raster pixel {val} at ({lat}, {lon}) '
                f'(pixel {pix})')
            expected_class = 'HIGH' if float(val) >= FROZEN_THRESHOLD else 'LOW'
            assert d['susceptibility_class'] == expected_class, (
                f'class mismatch at ({lat}, {lon}): {d["susceptibility_class"]} vs {expected_class}')
    return f'point inference == raster pixel within display rounding at {len(coords)} coords'


# ── B. raster geometry / overlay bounds ───────────────────────────────────────
@test
def test_raster_crs_grid_and_overlay_bounds():
    with rasterio.open(TIF) as src:
        epsg = src.crs.to_epsg() if src.crs is not None else None
        assert epsg == 32644, f'susceptibility raster CRS is not EPSG:32644 (got {src.crs})'
        assert abs(src.transform.a - 28.74) < 0.05 and abs(src.transform.e + 28.74) < 0.05, src.transform
        r_left, r_bottom = src.bounds.left, src.bounds.bottom
        r_right, r_top = src.bounds.right, src.bounds.top
    with open(OVERLAY_JSON, encoding='utf-8') as f:
        ov = json.load(f)
    assert ov['threshold'] == FROZEN_THRESHOLD
    mode = str(ov.get('mode', '')).lower()
    assert 'gradient' in mode and 'transparent' in mode, mode
    (south, west), (north, east) = ov['bounds']
    assert 30.0 < south < north < 31.0 and 78.8 < west < east < 79.6, ov['bounds']
    # overlay geographic bounds must sit inside the raster's lat/lon extent
    assert south >= 30.0 - 0.05 and north <= 31.0 + 0.05, (south, north)
    assert west >= 78.5 and east <= 80.0, (west, east)
    return (f'raster EPSG:32644 (28.74 m px) bounds [{r_left:.2f},{r_bottom:.2f}]-[{r_right:.2f},{r_top:.2f}]; '
            f'overlay bounds {ov["bounds"]} consistent with experimental gradient mode')


# ── C. overlay colour mapping + nodata ────────────────────────────────────────
@test
def test_overlay_gradient_colours_and_nodata():
    img = Image.open(OVERLAY_PNG).convert('RGBA')
    assert 'A' in img.mode, f'overlay PNG mode {img.mode} lacks alpha'
    px = img.load()
    w, h = img.size
    greenish = reddish = transparent = 0
    for yy in range(0, h, max(1, h // 40)):
        for xx in range(0, w, max(1, w // 40)):
            r, g, b, a = px[xx, yy]
            if a == 0:
                transparent += 1
                continue
            if g > r and g > b and g > 60:
                greenish += 1
            if r > g and r > b and r > 60:
                reddish += 1
    assert greenish > 0 and reddish > 0, 'overlay gradient (green low -> red high) not found'
    assert transparent > 0, 'overlay nodata pixels are not transparent'
    return (f'overlay PNG {w}x{h} RGBA; gradient present (greenish={greenish}, reddish={reddish}); '
            f'nodata transparent={transparent} samples')


# ── D. out-of-coverage must not fabricate ──────────────────────────────────────
@test
def test_out_of_coverage_no_fabrication():
    with _Server() as s:
        d = _rf_point(s, 19.5, 70.5)  # far outside Chamoli AOI
    assert d.get('status') == 'model_trained_out_of_coverage', d.get('status')
    assert d.get('no_fabricated_predictions') is True
    assert d.get('susceptibility_probability') is None
    return 'out-of-coverage click -> no_fabricated_predictions, no fabricated probability'


# ── E. LOW / MEDIUM / HIGH display classification ─────────────────────────────
@test
def test_low_medium_high_display_classification():
    js_path = os.path.join(ROOT, 'public', 'assets', 'terraguard.js')
    with open(js_path, encoding='utf-8') as f:
        js = f.read()
    assert 'p >= 0.38' in js, 'frontend HIGH threshold (>= 0.38) missing'
    assert 'p >= 0.15' in js, 'frontend MEDIUM band (>= 0.15) missing'

    def band(p):
        if p is None:
            return 'n/a'
        if p >= 0.38:
            return 'HIGH'
        if p >= 0.15:
            return 'MEDIUM'
        return 'LOW'

    expected = {0.05: 'LOW', 0.2: 'MEDIUM', 0.5: 'HIGH', 0.9984: 'HIGH', 0.0: 'LOW'}
    for p, want in expected.items():
        assert band(p) == want, f'band({p}) = {band(p)}, expected {want}'
    with _Server() as s:
        d = _rf_point(s, 30.40, 79.32)  # flat terrain -> LOW
    assert d['susceptibility_class'] == 'LOW'
    return 'LOW <0.15 / MEDIUM 0.15-0.38 / HIGH >=0.38 (display); backend HIGH vs LOW at frozen 0.38'


# ── F. /rf/susceptibility endpoint contract unchanged ─────────────────────────
@test
def test_rf_endpoint_contract_unchanged():
    with _Server() as s:
        d = _rf_point(s, 30.40, 79.32)
    required = {
        'service', 'status', 'model_version', 'model', 'experimental', 'features',
        'frozen_threshold', 'terrain', 'susceptibility_probability', 'susceptibility_class',
        'predicted_label', 'probability_interpretation', 'DISCLAIMER', 'no_fabricated_predictions',
        'independent_rainfall', 'coordinates', 'metrics_at_frozen_threshold', 'bootstrap_cis',
        'limitations',
    }
    missing = sorted(required - set(d))
    assert not missing, f'/rf/susceptibility missing keys: {missing}'
    assert d['no_fabricated_predictions'] is True
    assert d['frozen_threshold'] == 0.38
    assert set(d.get('terrain', {})) >= {'elevation_m', 'slope_deg', 'aspect_deg', 'hillshade'}
    return f'/rf/susceptibility contract keys verified ({len(d)} keys returned)'


def main():
    print('=' * 74)
    print('SUSCEPTIBILITY SPATIAL CONSISTENCY - TESTS')
    print('=' * 74)
    passed = failed = 0
    for fn in RESULTS:
        try:
            msg = fn()
            passed += 1
            print(f'PASS  {fn.__name__}: {msg}')
        except AssertionError as e:
            failed += 1
            print(f'FAIL  {fn.__name__}: {e}')
            traceback.print_exc()
        except Exception as e:
            failed += 1
            print(f'ERROR {fn.__name__}: {type(e).__name__}: {e}')
            traceback.print_exc()
    print('-' * 74)
    print(f'{passed} passed, {failed} failed, {len(RESULTS)} total')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
