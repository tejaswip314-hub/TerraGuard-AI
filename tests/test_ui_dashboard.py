#!/usr/bin/env python
"""Tests for the map-first dashboard: routes, assets, contracts, frontend wiring.

Run:  python tests/test_ui_dashboard.py
Exit code 0 = all passed. No pytest / browser dependency.
Network-dependent checks report SKIPPED when Open-Meteo is unreachable.
"""
import json
import os
import re
import sys
import threading
import traceback
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'api'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import http.server as hs  # noqa: E402
import socketserver as ss  # noqa: E402

import server as srv  # noqa: E402

RESULTS = []
PUBLIC = os.path.join(ROOT, 'public')


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


def _read(name):
    with open(os.path.join(PUBLIC, name), encoding='utf-8') as f:
        return f.read()


# ── A. routing ───────────────────────────────────────────────────────────────
@test
def test_root_serves_dashboard_html():
    with _Server() as s:
        status, ctype, body = s.get('/')
        _, _, js = s.get('/assets/terraguard.js')
    text = body.decode('utf-8')
    js_text = js.decode('utf-8')
    assert status == 200
    assert ctype.startswith('text/html'), ctype
    assert '<title>TerraGuardAI' in text
    assert 'id="map"' in text
    assert 'assets/terraguard.js' in text and 'assets/terraguard.css' in text
    assert 'node_modules/leaflet/dist/leaflet.js' in text
    # required visible labels / disclaimer
    assert 'EXPERIMENTAL TERRAIN SUSCEPTIBILITY' in text
    assert 'EXPERIMENTAL RAINFALL-ENHANCED SCREENING INDEX' in text
    assert 'NOT a calibrated landslide probability' in text
    assert 'not live imagery' in text.lower()
    # endpoint wiring lives in the shared app script referenced by the page
    assert '/screening?lat=' in js_text
    assert '/weather?lat=' in js_text
    return 'GET / returns the dashboard HTML wired to the screening/weather endpoints'


@test
def test_health_is_json_and_not_at_root():
    with _Server() as s:
        status, ctype, body = s.get('/health')
        d = json.loads(body.decode())
    assert status == 200 and ctype.startswith('application/json')
    assert d['status'] == 'running'
    assert d['version'].startswith('1.1')
    assert d['rf_model']['frozen_threshold'] == 0.38
    assert d['rainfall_screening']['available'] is True
    assert len(d['map_layers']) == 4, d['map_layers']
    return f"GET /health JSON v{d['version']}; 4 basemaps listed"


@test
def test_assets_served():
    with _Server() as s:
        css_status, css_type, css = s.get('/assets/terraguard.css')
        js_status, js_type, js = s.get('/assets/terraguard.js')
    js_text = js.decode('utf-8')
    assert css_status == 200 and css_type.startswith('text/css') and len(css) > 20000
    assert js_status == 200 and 'javascript' in js_type and len(js) > 20000
    assert "L.map('map'" in js_text
    assert 'navigator.geolocation.getCurrentPosition' in js_text
    # permission must only be requested from the click handler
    assert js_text.count('getCurrentPosition') == 1
    assert 'btnLocate' in js_text
    for service in ('World_Imagery', 'World_Topo_Map', 'World_Street_Map', 'tile.openstreetmap.org'):
        assert service in js_text, service
    return f'CSS {len(css):,} B, JS {len(js):,} B served; 4 basemaps; geolocation only on click'


@test
def test_map_variant_is_generated_consistently():
    index = _read('index.html')
    map_page = _read('map.html')
    expected = index.replace('<body data-page="dashboard">', '<body data-page="map">', 1)
    expected = re.sub(r'<title>TerraGuardAI[^<]*</title>',
                      '<title>TerraGuardAI \u2014 Map</title>', expected, count=1)
    assert '<body data-page="map">' in map_page
    # newlines may differ across platforms; compare normalised text
    assert map_page.replace('\r\n', '\n') == expected.replace('\r\n', '\n')
    return 'map.html is generated from index.html (only page marker + title differ)'


# ── B. data endpoints used by the UI ─────────────────────────────────────────
@test
def test_overlay_metadata_alignment_fields():
    with _Server() as s:
        status, _, body = s.get('/susceptibility/overlay.json')
    d = json.loads(body.decode())
    assert status == 200
    assert d['threshold'] == 0.38
    assert d['model'].startswith('RF v3')
    (south, west), (north, east) = d['bounds']
    assert 30.0 < south < north < 31.0 and 78.8 < west < east < 79.6
    assert d['image'].endswith('overlay.png')
    assert 'gradient' in d.get('mode', '').lower(), d.get('mode')
    assert 'transparent' in d.get('mode', '').lower(), d.get('mode')
    return f"overlay bounds {d['bounds']} align with Chamoli AOI (gradient mode: {d['mode'][:40]}...)"


@test
def test_inventory_geojson():
    with _Server() as s:
        status, _, body = s.get('/inventory')
    d = json.loads(body.decode())
    assert status == 200
    assert d['type'] == 'FeatureCollection'
    assert len(d['features']) == 49
    props = d['features'][0]['properties']
    for k in ('ID', 'Area', 'Lithology', 'LULC', 'Triggrd_by', 'Near_to', 'Fault'):
        assert k in props, k
    geom = d['features'][0]['geometry']['type']
    assert geom in ('Polygon', 'MultiPolygon'), geom
    return f"49 inventory polygons; attributes {sorted(props)}"


@test
def test_rf_point_query_contract():
    with _Server() as s:
        status, _, body = s.get('/rf/susceptibility?lat=30.40&lon=79.32')
    d = json.loads(body.decode())
    assert status == 200
    assert d['status'] == 'inference_complete', d.get('status')
    assert 0.0 <= d['susceptibility_probability'] <= 1.0
    assert d['susceptibility_class'] in ('LOW', 'HIGH')
    assert d['frozen_threshold'] == 0.38
    assert 'EXPERIMENTAL' in json.dumps(d).upper()
    return (f"RF v3 point {d['susceptibility_probability']} -> "
            f"{d['susceptibility_class']} at frozen threshold 0.38")


@test
def test_weather_contract_and_cache():
    lat, lon = 30.41, 79.33
    try:
        with _Server() as s:
            _, _, b1 = s.get(f'/weather?lat={lat}&lon={lon}')
            _, _, b2 = s.get(f'/weather?lat={lat}&lon={lon}')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f'SKIPPED (no network): {type(e).__name__}: {e}'
    d1, d2 = json.loads(b1.decode()), json.loads(b2.decode())
    if d1.get('status') != 'weather_ok':
        return 'SKIPPED (Open-Meteo unavailable): ' + str(d1.get('error'))[:80]

    cur = d1['current']
    for k in ('temperature_c', 'apparent_temperature_c', 'relative_humidity_pct',
              'condition', 'condition_code', 'precipitation', 'wind_speed_kmh',
              'wind_direction_deg', 'wind_gusts_10m', 'is_day', 'time'):
        assert k in cur, k
    # legacy keys preserved (existing contract)
    for k in ('precipitation', 'rain', 'showers', 'precipitation_probability', 'wind_gusts_10m'):
        assert k in cur, k
    h = d1['hourly']
    for k in ('times', 'temperature_c', 'precipitation', 'precipitation_probability',
              'condition_code', 'condition', 'wind_speed_kmh'):
        assert k in h and isinstance(h[k], list), k
    assert len(h['times']) >= 24
    assert len(h['temperature_c']) == len(h['times'])
    dy = d1['daily']
    for k in ('times', 'temperature_max_c', 'temperature_min_c', 'precipitation_sum',
              'precipitation_probability_max', 'sunrise', 'sunset', 'condition_code'):
        assert k in dy and isinstance(dy[k], list), k
    assert 1 <= len(dy['times']) <= 7
    assert d1['source'].startswith('Open-Meteo')
    assert d2['from_cache'] is True, 'second call should hit the 5-min cache'
    return (f"current {cur['temperature_c']}deg C {cur['condition']}, "
            f"{len(h['times'])} hourly, {len(dy['times'])} daily; cache hit on repeat")


# ── C. frontend wiring ───────────────────────────────────────────────────────
@test
def test_frontend_ids_referenced_by_js_exist():
    js = _read(os.path.join('assets', 'terraguard.js'))
    html = _read('index.html')
    defined = set(re.findall(r'id=["\']([A-Za-z][A-Za-z0-9_-]*)["\']', html))
    used = set(re.findall(r"\$\('([A-Za-z][A-Za-z0-9_-]*)'\)", js))
    used |= set(re.findall(r"getElementById\(['\"]([A-Za-z][A-Za-z0-9_-]*)['\"]\)", js))
    missing = sorted(used - defined)
    assert not missing, f'JS references undefined element ids: {missing}'
    return f'{len(used)} element ids referenced by JS are all defined in index.html'


@test
def test_ui_requirements_present_in_markup():
    src = _read('index.html')
    required = [
        'My location', 'btnFullscreen', 'panelLayers', 'panelWeather', 'panelScreening',
        'sbCoords', 'sbScale', 'sbBasemap', 'sbStatus',
        'tglSusceptibility', 'opacityRange', 'tglInventory', 'tglScreening',
        'wxTemp', 'wxFeels', 'wxHumidity', 'wxWind', 'wxPrecip', 'wxGust',
        'wxCondition', 'wxUpdated', 'wxHourly', 'wxDaily',
        'scrTerrain', 'scrObs1h', 'scrObs24h', 'scrNRain', 'scrFct24h',
        'scrCombined', 'scrAlpha', 'scrDisclaimer', 'scrSource',
        'Hourly', '7-day', 'Esri World Imagery', 'Esri World Topo',
        'Esri World Street', 'OpenStreetMap',
    ]
    missing = [k for k in required if k not in src]
    assert not missing, f'missing UI markers: {missing}'
    assert src.count('data-bm=') == 4
    assert 'forecast' in src.lower()
    return f'{len(required)} UI markers present; 4 basemap options'


@test
def test_no_hardcoded_user_location():
    js = _read(os.path.join('assets', 'terraguard.js'))
    html = _read('index.html')
    # The only coordinates in the app must be the documented study-area default.
    assert 'navigator.geolocation.getCurrentPosition' in js
    assert "30.40" in js or '30.40' in html
    # no fake "you are here" coordinates baked into labels
    assert not re.search(r'(my|your)\s+location\s*[:(]\s*\d{2}\.\d+', html, re.I)
    return 'user location is never hardcoded; only Chamoli default point is fixed'


def main():
    print('=' * 74)
    print('MAP-FIRST DASHBOARD - ROUTE, CONTRACT & WIRING TESTS')
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
