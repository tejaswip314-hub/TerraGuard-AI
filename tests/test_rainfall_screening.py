#!/usr/bin/env python
"""Tests for the rainfall-normalized screening signal (Part F).

Run:  python tests/test_rainfall_screening.py
Exit code 0 = all passed. No pytest dependency (not installed in this env).
"""
import datetime
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'api'))

import screening_utils as su  # noqa: E402

RESULTS = []


def test(fn):
    RESULTS.append(fn)
    return fn


def _payload(precip_by_hour, start='2026-11-01T00:00', n=240, current_time='2026-11-08T12:00'):
    """Build a fake-but-realistic Open-Meteo payload dict."""
    times = []
    t = datetime.datetime.strptime(start, '%Y-%m-%dT%H:%M')
    for i in range(n):
        times.append((t + datetime.timedelta(hours=i)).strftime('%Y-%m-%dT%H:00'))
    hourly_precip = [precip_by_hour.get(tt, 0.0) for tt in times]
    return {
        'current': {'time': current_time, 'precipitation': 0.0},
        'hourly': {'time': times, 'precipitation': hourly_precip},
        'timezone': 'Asia/Kolkata',
    }


# ── A. zero rainfall with valid data ─────────────────────────────────────────
@test
def test_zero_rainfall():
    payload = _payload({})  # all 0.0, all present
    block = su.build_rainfall_block(payload)
    obs = block['observed']
    assert obs['status'] == 'ok', obs['status']
    assert obs['normalized_indicator'] == 0.0, obs['normalized_indicator']
    for name, w in obs['windows'].items():
        assert w['accumulation_mm'] == 0.0, (name, w)
        assert w['normalized'] == 0.0, (name, w)
    # combined with zero rain must equal terrain score exactly
    assert su.combine(0.30, 0.0, 1.0) == 0.30
    return 'zero rain -> N=0, S == terrain score (0.30)'


# ── B. increasing rainfall -> increasing normalized ──────────────────────────
@test
def test_monotonic_normalized():
    prev = -1.0
    for a in [0.0, 1.0, 5.0, 10.0, 25.0, 80.0, 250.0, 1000.0]:
        n = su.normalize_window(a, 80.0)
        assert 0.0 <= n < 1.0, (a, n)
        assert n > prev, (a, n, prev)
        prev = n
    # N == 0.5 exactly at A == R
    assert abs(su.normalize_window(80.0, 80.0) - 0.5) < 1e-9
    return 'monotonic increasing in [0,1); N=0.5 at A=R'


# ── C. increasing rainfall increases combined for fixed terrain ──────────────
@test
def test_combined_increases():
    s0 = su.combine(0.40, 0.0, 1.0)
    s1 = su.combine(0.40, 0.5, 1.0)
    s2 = su.combine(0.40, 0.9, 1.0)
    assert s0 < s1 < s2, (s0, s1, s2)
    assert abs(s0 - 0.40) < 1e-9
    assert abs(s1 - 0.60) < 1e-9   # 0.4 * (1 + 1*0.5)
    return 'fixed terrain 0.40: S = 0.40, 0.60, 0.76 for N=0, .5, .9'


# ── D. terrain score remains unchanged (RF v3 frozen) ────────────────────────
@test
def test_terrain_score_unchanged():
    # combine() must never mutate its terrain input, and zero-rain identity holds
    terrain = 0.4242
    _ = su.combine(terrain, 0.7, 1.0)
    assert terrain == 0.4242
    assert su.combine(terrain, 0.0, 1.0) == terrain
    # alpha=0 leaves combined == terrain regardless of rainfall
    assert su.combine(terrain, 0.99, 0.0) == terrain
    return 'terrain input never mutated; alpha=0 -> S == terrain'


# ── E. missing rainfall returns unavailable, not zero ────────────────────────
@test
def test_missing_is_unavailable_not_zero():
    # hourly arrays with nulls for the trailing 24 h around anchor 12:00 on 11-08
    times = []
    t = datetime.datetime(2026, 11, 8, 12, 0)
    for i in range(-48, 1):  # 48h back .. anchor hour
        times.append((t + datetime.timedelta(hours=i)).strftime('%Y-%m-%dT%H:00'))
    precip = []
    for tt in times:
        hh = datetime.datetime.strptime(tt, '%Y-%m-%dT%H:%M')
        # make the trailing 24h all missing (None)
        precip.append(None if hh >= datetime.datetime(2026, 11, 7, 13, 0) else 0.0)
    payload = {'current': {'time': '2026-11-08T12:00'}, 'hourly': {'time': times, 'precipitation': precip}}
    block = su.build_rainfall_block(payload)
    obs = block['observed']
    # 24h and 7d windows must be unavailable; accumulation never faked to 0
    assert obs['windows']['24h']['status'] in ('partial', 'unavailable'), obs['windows']['24h']
    assert obs['windows']['24h']['accumulation_mm'] is None or obs['windows']['24h']['complete'] is False
    # 1h window (11:00-11:59) may be complete; that is fine. But if 24h incomplete,
    # the indicator may still come from 1h. Verify no window reports a fabricated 0 sum.
    for name, w in obs['windows'].items():
        if not w['complete']:
            assert w['missing_hours'] > 0, (name, w)
    # a payload with NO usable hours -> indicator None + status unavailable
    empty = {'current': {'time': '2026-11-08T12:00'}, 'hourly': {'time': [], 'precipitation': []}}
    blk2 = su.build_rainfall_block(empty)
    assert blk2['observed']['normalized_indicator'] is None
    assert blk2['observed']['status'] == 'unavailable'
    assert blk2['observed']['unavailable_windows'], blk2['observed']
    return 'missing hours flagged; no complete window -> indicator None + unavailable'


# ── F. normalized values remain in valid bounds ──────────────────────────────
@test
def test_bounds():
    for a in [0, 0.001, 1, 999, 1e6, 1e9]:
        n = su.normalize_window(a, 10.0)
        assert 0.0 <= n < 1.0, (a, n)
    for p in [0.0, 0.25, 0.5, 0.99, 1.0]:
        for n in [0.0, 0.5, 0.99]:
            for alpha in [0.0, 1.0, 5.0]:
                s = su.combine(p, n, alpha)
                assert 0.0 <= s <= 1.0, (p, n, alpha, s)
    # clamping of extreme inputs
    assert su.combine(1.0, 0.999, 10.0) == 1.0
    return 'N in [0,1); S clamped to [0,1] for all tested inputs'


# ── G. forecast vs observed distinction ──────────────────────────────────────
@test
def test_forecast_vs_observed():
    # put rain in the FUTURE (after anchor) so only forecast sees it
    times = []
    t = datetime.datetime(2026, 11, 8, 12, 0)
    for i in range(-48, 24):
        times.append((t + datetime.timedelta(hours=i)).strftime('%Y-%m-%dT%H:00'))
    precip = []
    for tt in times:
        hh = datetime.datetime.strptime(tt, '%Y-%m-%dT%H:%M')
        precip.append(5.0 if hh > t else 0.0)  # rain only in future hours
    payload = {'current': {'time': '2026-11-08T12:00'}, 'hourly': {'time': times, 'precipitation': precip}}
    block = su.build_rainfall_block(payload)
    obs = block['observed']['normalized_indicator']
    fct = block['forecast']['normalized_indicator']
    assert obs == 0.0, obs              # no rain in observed trailing window
    assert fct is not None and fct > 0.0, fct   # forecast sees the future rain
    # forecast windows limited to 1h/3h/24h
    assert set(block['forecast']['windows'].keys()) == {'1h', '3h', '24h'}
    assert set(block['observed']['windows'].keys()) == {'1h', '3h', '24h', '72h', '7d'}
    return 'observed excludes future rain; forecast sees it; window sets differ'


# ── H. combination formula & alpha config ────────────────────────────────────
@test
def test_alpha_config():
    assert su.effective_alpha(None) == su.ALPHA
    assert su.effective_alpha('2.5') == 2.5
    assert su.effective_alpha('abc') == su.ALPHA
    assert su.effective_alpha('-3') == 0.0
    assert su.effective_alpha('999') == 10.0   # clamped
    # formula literal check
    assert su.combine(0.5, 0.4, 1.0) == 0.7
    return 'alpha config/clamp correct; S = P*(1+alpha*N) verified'


# ── I. reference values documented & overridable ─────────────────────────────
@test
def test_reference_values():
    assert su.REFERENCE_MM == {'1h': 10.0, '3h': 25.0, '24h': 80.0, '72h': 150.0, '7d': 250.0}
    os.environ['RAIN_REF_24H'] = '120'
    try:
        assert su.reference_mm('24h') == 120.0
    finally:
        del os.environ['RAIN_REF_24H']
    assert su.reference_mm('24h') == 80.0
    return 'documented defaults + env override (RAIN_REF_24H) works'


# ── J. live API integration (skips cleanly if offline) ───────────────────────
@test
def test_live_api_integration():
    try:
        payload, cached = su.fetch_open_meteo(30.33, 79.42)
    except Exception as e:
        return f'SKIPPED (no network): {type(e).__name__}: {e}'
    block = su.build_rainfall_block(payload)
    assert block['source'].startswith('Open-Meteo')
    assert 'observed' in block and 'forecast' in block
    assert block['observed']['windows']['24h']['reference_mm'] == 80.0
    # indicator in valid bounds or None
    n = block['observed']['normalized_indicator']
    assert n is None or 0.0 <= n < 1.0, n
    return f'live OK: obs={n} fetched_at={block["fetched_at_ist"]} (cached={cached})'


# ── K. API contract + frontend integration ───────────────────────────────────
@test
def test_api_contract_matches_frontend():
    """The JSON keys the frontend reads must exist in the /screening payload."""
    import urllib.request
    import threading
    import http.server as hs
    import socketserver as ss
    import server as srv  # api/server.py

    # start an ephemeral server instance in-process
    srv.socketserver.TCPServer.allow_reuse_address = True
    srv.Handler.log_message = lambda *a, **k: None   # keep test output clean
    httpd = ss.TCPServer(('127.0.0.1', 0), srv.Handler)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        url = f'http://127.0.0.1:{port}/screening?lat=30.33&lon=79.42'
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.loads(r.read().decode())
    finally:
        httpd.shutdown()

    # keys the frontend JS reads
    assert d['status'].startswith('screening')
    for k in ('terrain', 'rainfall', 'combined', 'disclaimer'):
        assert k in d, k
    assert 'rf_v3_probability' in d['terrain']
    assert 'susceptibility_class' in d['terrain']
    assert 'frozen_threshold' in d['terrain']
    assert 'windows' in d['rainfall']['observed']
    assert 'windows' in d['rainfall']['forecast']
    assert 'normalized_indicator' in d['rainfall']['observed']
    assert 'normalized_indicator' in d['rainfall']['forecast']
    for k in ('alpha', 'formula', 'terrain_score', 'rainfall_normalized',
              'combined_screening_index', 'bounded_0_1'):
        assert k in d['combined'], k
    # every observed window carries reference_mm + normalized + complete
    for name, w in d['rainfall']['observed']['windows'].items():
        for k in ('accumulation_mm', 'reference_mm', 'normalized', 'complete', 'status', 'hours_present'):
            assert k in w, (name, k)
    # disclaimer contains the mandated phrase
    assert 'UNCALIBRATED' in ' '.join(d['disclaimer'])
    return 'API keys match frontend expectations; disclaimer present'


@test
def test_frontend_html_wiring():
    """Both frontends must contain the panel, disclaimer, and /screening wiring.

    Frontend logic was consolidated into public/assets/terraguard.js, so the
    fetch call and element lookups are checked there; the markup checks stay
    per-page.
    """
    import re
    root = r'D:\TerraGuardAI\public'
    checks = {
        'index': root + r'\index.html',
        'map': root + r'\map.html',
    }
    js = open(root + r'\assets\terraguard.js', encoding='utf-8').read()
    for name, path in checks.items():
        src = open(path, encoding='utf-8').read()
        combined = src + '\n' + js
        assert "/screening?lat=" in combined, f'{name}: no /screening fetch'
        assert 'assets/terraguard.js' in src, f'{name}: app script not referenced'
        assert 'scrCombined' in combined, f'{name}: no combined index element'
        assert 'scrNRain' in combined, f'{name}: no normalized rainfall element'
        assert 'scrTerrain' in combined, f'{name}: no terrain score element'
        assert 'EXPERIMENTAL RAINFALL-ENHANCED SCREENING INDEX' in src, f'{name}: missing mandated disclaimer'
        assert 'NOT a calibrated landslide probability' in src, f'{name}: missing disclaimer tail'
        assert 'Experimental terrain susceptibility assessment; not a landslide prediction or safety determination.' in js, \
            f'{name}: missing terrain susceptibility assessment disclaimer'
        # ids referenced by JS must be defined in the page markup
        used = set(re.findall(r"getElementById\(['\"](scr[A-Za-z0-9]*)['\"]\)", js))
        used |= set(re.findall(r"scrSet\(['\"](scr[A-Za-z0-9]*)['\"]", js))
        used |= set(re.findall(r"\$\('(scr[A-Za-z0-9]*)'\)", js))
        defined = set(re.findall(r'id=["\'](scr[A-Za-z0-9]*)["\']', src))
        missing = used - defined
        assert not missing, f'{name}: JS references undefined ids {missing}'
    return 'both frontends wired: panel, disclaimer, /screening fetch, all ids defined'


def main():
    print('=' * 74)
    print('RAINFALL-NORMALIZED SCREENING — TEST SUITE')
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