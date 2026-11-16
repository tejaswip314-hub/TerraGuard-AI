/* ============================================================================
   TerraGuardAI — map-first situational awareness front-end
   - Leaflet map with Esri satellite / topo / streets + OSM
   - RF v3 terrain susceptibility raster + point inference (frozen model)
   - Landslide inventory (genuine GeoJSON)
   - Live Open-Meteo weather (current + forecast) and rainfall screening
   - Geolocation, fullscreen, collapsible panels, status bar
   No frameworks. No fabricated data.
   ========================================================================== */

(function () {
  'use strict';

  /* ── tiny helpers ─────────────────────────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const on = (el, ev, fn, opts) => { if (el) el.addEventListener(ev, fn, opts); };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
  const num = (v) => (v === null || v === undefined || v === '' || !isFinite(Number(v))) ? null : Number(v);
  const fixed = (v, d) => (num(v) === null ? '—' : num(v).toFixed(d));

  /* ── Configuration ─────────────────────────────────────────────────────
     API base URL — default is same-origin (empty string), so localhost and
     a same-origin Render deployment both work unchanged. To point the
     frontend at a separate API host, set either:
       <meta name="terraguard-api-base" content="https://api.example.com">
     before app.js loads, OR window.TERRAGUARD_API_BASE in an inline script.
     All requests ("/health", "/weather?...", "/inventory", …) are routed
     through this base; absolute URLs (starting with http://, https://, //)
     are left untouched. */
  const TERRAGUARD_API_BASE = (() => {
    try {
      const meta = typeof document !== 'undefined'
        ? document.querySelector('meta[name="terraguard-api-base"]')
        : null;
      const fromMeta = meta && meta.content ? meta.content.trim() : '';
      const fromWindow = (typeof window !== 'undefined' && window.TERRAGUARD_API_BASE)
        ? String(window.TERRAGUARD_API_BASE).trim() : '';
      return (fromMeta || fromWindow || '').replace(/\/+$/, ''); // strip trailing slashes
    } catch (e) {
      return '';
    }
  })();

  function apiUrl(url) {
    if (!url) return url;
    if (/^(https?:)?\/\//i.test(url) || url.startsWith('data:')) return url; // absolute
    if (url.startsWith('/') && TERRAGUARD_API_BASE) return TERRAGUARD_API_BASE + url;
    return url; // relative / unknown scheme — hand to fetch as-is
  }

  async function getJSON(url, timeoutMs) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs || 25000);
    try {
      const res = await fetch(apiUrl(url), { signal: ctrl.signal, headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  function toast(message, kind) {
    const wrap = $('toastWrap');
    if (!wrap) return;
    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.textContent = message;
    wrap.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 5200);
    setTimeout(() => el.remove(), 5600);
  }

  function setStatus(text, kind) {
    const el = $('sbStatus');
    if (el) el.textContent = text;
    const dot = $('sbStatusDot');
    if (dot) dot.className = 'dot' + (kind === 'err' ? ' err' : kind === 'warn' ? ' warn' : '');
  }

  /* ── configuration ────────────────────────────────────────────────────── */
  const STUDY_AREA = {
    name: 'Chamoli · Uttarakhand',
    // RF v3 / inventory AOI (bbox + 2 km around the 1999 inventory)
    bounds: [[30.10, 78.90], [30.52, 79.45]],
    center: [30.30, 79.17]
  };
  const INDIA_BOUNDS = [
    [6.0, 68.0],   // SW
    [35.5, 68.0],  // NW
    [35.5, 97.5],  // NE
    [6.0, 97.5],   // SE
    [6.0, 68.0]    // close
  ];
  const INDIA_CENTER = [22.5, 79.0];
  const DEFAULT_POINT = { lat: 30.40, lon: 79.32, label: 'Chamoli · default point' };

  const BASEMAPS = {
    satellite: {
      label: 'Esri World Imagery',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics, GIS User Community',
      maxZoom: 19
    },
    topo: {
      label: 'Esri World Topographic',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Tiles &copy; Esri, HERE, Garmin, USGS, NGA',
      maxZoom: 19
    },
    streets: {
      label: 'Esri World Street Map',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Tiles &copy; Esri, HERE, Garmin, USGS, NGA',
      maxZoom: 19
    },
    osm: {
      label: 'OpenStreetMap',
      url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
      maxZoom: 19
    }
  };

  const state = {
    page: document.body.dataset.page === 'map' ? 'map' : 'dashboard',
    selected: Object.assign({}, DEFAULT_POINT),
    basemapKey: 'osm',
    overlayVisible: true,
    overlayOpacity: 0.40,
    inventoryVisible: true,
    model: 'india',          // 'india' (default, national RF) | 'chamoli' (pilot)
    coverageVisible: false,
    coverageLayer: null,
    weatherToken: 0,
    screeningToken: 0,
    rfToken: 0,
    lastRf: null,
    lastScreening: null,
    clickTimer: null,
    locationAccuracy: null,
    // Automatic basemap fallback state
    tileErrorCounts: {}, // key -> count of consecutive tile failures
    fallbackThreshold: 5, // number of tile errors before auto-switching to OSM
    inFallback: false // prevents fallback loops
  };

  /* ── map ──────────────────────────────────────────────────────────────── */
  let map = null;
  let basemapLayer = null;
  let susOverlay = null;
  let inventoryLayer = null;
  let indiaLayer = null;

  const pointLayer = L.layerGroup();
  const locationLayer = L.layerGroup();

  function isMobile() { return window.matchMedia('(max-width: 760px)').matches; }

  function initMap() {
    const mapEl = $('map');
    if (!mapEl) return;
    /* Ensure the container has a size BEFORE Leaflet reads it */
    if (mapEl.offsetWidth === 0) {
      mapEl.style.minHeight = '300px';
    }

    map = L.map('map', {
      zoomControl: false,
      attributionControl: false,
      preferCanvas: true,
      minZoom: 2,
      maxZoom: 18,
      worldCopyJump: true,
      zoomSnap: 0.5,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 90
    });

    map.setView(INDIA_CENTER, 5);

    basemapLayer = createBasemap(state.basemapKey);
    basemapLayer.addTo(map);

    setStatus('Loading basemap…', 'warn');
    basemapLayer.on('load', () => {
      setStatus('Open-Meteo live · RF susceptibility · experimental screening', 'ok');
      map.invalidateSize();
    });

    /* ResizeObserver: invalidate Leaflet whenever #map's box changes
       (initial layout, fonts, panel drag, window resize, DevTools docking, fullscreen) */
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(() => { if (map) map.invalidateSize(); });
      ro.observe(mapEl);
      /* also observe the parent layout shell in case it resizes independently */
      const shell = mapEl.closest('.map-shell');
      if (shell) ro.observe(shell);
    }

    L.control.attribution({
      position: 'bottomright',
      prefix: 'TerraGuardAI · <a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>'
    }).addTo(map);
    L.control.zoom({ position: 'bottomleft', zoomInTitle: 'Zoom in', zoomOutTitle: 'Zoom out' }).addTo(map);
    L.control.scale({ position: 'bottomleft', imperial: false, maxWidth: 120 }).addTo(map);

    pointLayer.addTo(map);
    locationLayer.addTo(map);

    // Load India boundary overlay
    loadIndiaBoundary();

    fitIndiaArea();

    map.on('zoomend moveend', updateScale);
    map.on('zoomend', () => { $('sbZoom').textContent = 'z' + map.getZoom(); });
    map.on('mousemove', onMouseMove);
    map.on('click', (e) => scheduleSelection(e.latlng.lat, e.latlng.lng,
      e.latlng.lat.toFixed(5) + '°N, ' + e.latlng.lng.toFixed(5) + '°E'));
    map.on('mouseout', () => { if (state.selected) updateCoords(state.selected.lat, state.selected.lon); });

    updateScale();
    $('sbZoom').textContent = 'z' + map.getZoom();
  }

  function createBasemap(key) {
    const b = BASEMAPS[key] || BASEMAPS.osm;
    const layer = L.tileLayer(b.url, {
      maxZoom: 19,
      maxNativeZoom: b.maxZoom,
      attribution: b.attribution,
      crossOrigin: "anonymous",
      detectRetina: false
    });
    layer.on('error', (e) => {
        console.log('Tile layer error for key: ' + key);
        if (typeof toast === 'function') {
            toast('Tile layer failed to load: ' + key, 'warn');
        }
    });
    layer.on('add', () => {
        console.log('Basemap loaded: ' + key);
    });
    layer.on('tileerror', (e) => {
        console.log('Tile error for key: ' + key);
    });
    return layer;
  }

  function fitStudyArea() {
    const pad = isMobile()
      ? { paddingTopLeft: [24, 90], paddingBottomRight: [24, 50] }
      : { paddingTopLeft: [320, 40], paddingBottomRight: [400, 50] };
    try {
      map.fitBounds(L.latLngBounds(STUDY_AREA.bounds), Object.assign({ animate: false }, pad));
    } catch (e) {
      map.setView(STUDY_AREA.center, 10);
    }
  }

  function fitIndiaArea() {
    try {
      map.fitBounds(L.latLngBounds(INDIA_BOUNDS), { animate: false });
    } catch (e) {
      map.setView(INDIA_CENTER, 5);
    }
  }

  function zoomToIndia() {
    fitIndiaArea();
  }

  function zoomToChamoli() {
    fitStudyArea();
  }

  // Approximate state boundaries (EPSG:4326) for navigation
  const STATE_BOUNDS = {
    maharashtra: [[15.6, 72.6], [22.0, 72.6], [22.0, 80.9], [15.6, 80.9], [15.6, 72.6]],
    gujarat: [[20.0, 68.0], [24.6, 68.0], [24.6, 73.0], [20.0, 73.0], [20.0, 68.0]],
    rajasthan: [[23.3, 68.0], [30.1, 68.0], [30.1, 78.3], [23.3, 78.3], [23.3, 68.0]],
    uttarpradesh: [[24.0, 78.0], [31.3, 78.0], [31.3, 84.3], [24.0, 84.3], [24.0, 78.0]],
    westbengal: [[22.5, 86.8], [27.1, 86.8], [27.1, 89.3], [22.5, 89.3], [22.5, 86.8]],
    tamilnadu: [[8.0, 76.5], [13.0, 76.5], [13.0, 80.2], [8.0, 80.2], [8.0, 76.5]],
    karnataka: [[11.6, 74.0], [18.5, 74.0], [18.5, 78.1], [11.6, 78.1], [11.6, 74.0]],
    kerala: [[8.0, 74.9], [13.0, 74.9], [13.0, 77.4], [8.0, 77.4], [8.0, 74.9]]
  };

  function zoomToState(stateKey) {
    const bounds = STATE_BOUNDS[stateKey];
    if (!bounds) return;
    try {
      map.fitBounds(L.latLngBounds(bounds), { animate: false });
    } catch (e) {
      map.setView(INDIA_CENTER, 5);
    }
  }

  function loadIndiaBoundary() {
    getJSON('/data/processed/india_boundary_epsg4326.geojson', 10000)
      .then((gj) => {
          indiaLayer = L.geoJSON(gj, {
          style: {
            color: '#b3e5fc',
            weight: 2,
            opacity: 0.8,
            fillColor: '#b3e5fc',
            fillOpacity: 0.03
          },
          onEachFeature: (feature, layer) => {
            layer.bindPopup('<strong>' + (feature.properties?.name || 'India') + '</strong>');
          }
        });
        if (state.overlayVisible) indiaLayer.addTo(map);
      })
      .catch((e) => {
        toast('India boundary overlay unavailable: ' + e.message, 'warn');
      });
  }

  function setBasemap(key) {
    if (!BASEMAPS[key]) return;
    state.basemapKey = key;
    if (basemapLayer) map.removeLayer(basemapLayer);
    basemapLayer = createBasemap(key);
    basemapLayer.addTo(map);
    // keep overlays above the fresh tile layer
    if (susOverlay && state.overlayVisible && !map.hasLayer(susOverlay)) susOverlay.addTo(map);
    if (inventoryLayer && state.inventoryVisible && !map.hasLayer(inventoryLayer)) inventoryLayer.addTo(map);

    document.querySelectorAll('.bm-btn').forEach((b) => {
      b.classList.toggle('active', b.dataset.bm === key);
    });
    $('sbBasemap').textContent = BASEMAPS[key].label;
    if (isMobile()) closeSheets();
  }

  /* ── RF model selector + coverage footprint ───────────────────────────── */
  function setModel(key, opts) {
    opts = opts || {};
    if (key !== 'india' && key !== 'chamoli') return;
    state.model = key;

    document.querySelectorAll('.model-btn').forEach((b) => {
      const on = b.dataset.model === key;
      b.classList.toggle('active', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
    });

    const susLabel = $('susLabel');
    if (susLabel) susLabel.textContent = key === 'india'
      ? 'India RF v1 · frozen threshold 0.402'
      : 'Chamoli pilot RF v3 · frozen threshold 0.38';

    const modelNote = $('modelNote');
    if (modelNote) modelNote.textContent = key === 'india'
      ? 'RF v1-national · ~1 km grid · GSI national inventory · GroupKFold by tile · OOF ROC-AUC 0.93'
      : 'Chamoli pilot · ~30 m · 1999 Mw 6.6 event only · threshold 0.38 · NOT validated elsewhere';

    const legendNote = document.querySelector('#stateNavSection + .scr-note');

    // Reload the susceptibility overlay for the selected model
    if (susOverlay) { map.removeLayer(susOverlay); susOverlay = null; }
    loadSusceptibility();

    // Re-query RF + screening at the currently selected point
    if (!opts.skipRequery) selectPoint(state.selected.lat, state.selected.lon, state.selected.label, { skipMarker: true });
  }

  function wireModelPicker() {
    document.querySelectorAll('.model-btn').forEach((btn) => {
      btn.addEventListener('click', () => setModel(btn.dataset.model));
    });

    on($('tglCoverage'), 'change', (e) => {
      state.coverageVisible = e.target.checked;
      if (state.coverageVisible) {
        loadCoverage();
      } else if (state.coverageLayer) {
        map.removeLayer(state.coverageLayer);
      }
    });
  }

  async function loadCoverage() {
    try {
      const gj = await getJSON('/coverage', 30000);
      if (!state.coverageVisible) return;
      if (state.coverageLayer) { map.removeLayer(state.coverageLayer); state.coverageLayer = null; }
      state.coverageLayer = L.geoJSON(gj, {
        style: () => ({
          color: '#f59e0b',
          weight: 1.2,
          opacity: 0.85,
          fillColor: '#f59e0b',
          fillOpacity: 0.08,
          interactive: false,
        }),
      }).addTo(map);
      setStatus('SRTM coverage footprint (' + gj.features.length + ' tiles) shown', 'ok');
    } catch (e) {
      toast('Coverage layer unavailable: ' + e.message, 'warn');
      const box = $('tglCoverage');
      if (box) box.checked = false;
      state.coverageVisible = false;
    }
  }

  /* ── Global location search (OpenStreetMap Nominatim, free) ─────────────
     - debounced, rate-limited, no API key required
     - results shown as suggestion dropdown; Enter / arrows / Escape work
     - selecting a result flies the map and hits the EXISTING RF endpoint */
  const SEARCH = {
    url: 'https://nominatim.openstreetmap.org/search',
    limit: 6,
    debounceMs: 350,
    minLen: 3,
    timer: null,
    abort: null,
    results: [],
    index: -1,
    token: 0,
  };

  function searchShortLabel(item) {
    const a = item.address || {};
    return a.city || a.town || a.village || a.suburb || a.county || a.state || a.country || (item.display_name || 'Selected location').split(',')[0].trim();
  }

  function searchContext(item) {
    const a = item.address || {};
    const parts = [];
    if (a.state) parts.push(a.state);
    if (a.country) parts.push(a.country);
    else if (a.country_code) parts.push(String(a.country_code).toUpperCase());
    if (a.road && !parts.includes(a.road)) parts.unshift(a.road);
    return parts.slice(0, 3).join(' · ') || (item.display_name || '').split(',').slice(1).join(',').trim();
  }

  function zoomForResult(item) {
    const t = (item.type || '').toLowerCase();
    const c = (item.class || '').toLowerCase();
    if (['country', 'continent', 'state', 'region'].includes(t) || c === 'boundary') return 6;
    if (['city', 'town', 'county'].includes(t)) return 11;
    if (['village', 'suburb', 'hamlet', 'quarter', 'city_district'].includes(t)) return 12;
    if (c === 'place') return 12;
    return 14; // POI / address / amenity — closer look
  }

  function renderSearchResults(list, query, state) {
    const box = $('searchDropdown');
    if (!box) return;
    if (state === 'loading') {
      box.innerHTML = '<div class="search-status"><span class="search-spinner"></span>Searching for “' + esc(query || '…') + '”…</div>';
      box.hidden = false;
      return;
    }
    if (state === 'error') {
      box.innerHTML = '<div class="search-status error">Search unavailable — check network.</div>';
      box.hidden = false;
      return;
    }
    if (!list || !list.length) {
      box.innerHTML = '<div class="search-status empty">No results for “' + esc(query || '') + '”. Try a different place or spelling.</div>';
      box.hidden = false;
      return;
    }
    box.innerHTML = list.map((item, i) => {
      const title = esc(item.display_name ? item.display_name.split(',')[0] : 'Unknown place');
      const ctx = esc(searchContext(item));
      const type = esc((item.type || item.class || 'place').replace(/_/g, ' '));
      return '<button class="search-item" role="option" data-index="' + i + '" aria-selected="false">' +
        '<span class="search-item-type">' + type + '</span>' +
        '<span class="search-item-text"><span class="search-item-title">' + title + '</span>' +
        (ctx ? '<span class="search-item-sub">' + ctx + '</span>' : '') +
        '</span>' +
      '</button>';
    }).join('');
    box.hidden = false;
  }

  function closeSearchDropdown(clearResults) {
    const box = $('searchDropdown');
    if (box) box.hidden = true;
    if (clearResults) {
      SEARCH.results = [];
      SEARCH.index = -1;
    }
  }

  function setActiveSearchItem(i) {
    const box = $('searchDropdown');
    if (!box) return;
    const items = box.querySelectorAll('.search-item');
    items.forEach((el, j) => {
      el.classList.toggle('active', j === i);
      el.setAttribute('aria-selected', j === i ? 'true' : 'false');
    });
    if (i >= 0 && items[i]) items[i].scrollIntoView({ block: 'nearest' });
  }

  async function runSearch(q) {
    const token = ++SEARCH.token;
    renderSearchResults(null, q, 'loading');

    if (SEARCH.abort) { try { SEARCH.abort.abort(); } catch (e) {} }
    const ctrl = new AbortController();
    SEARCH.abort = ctrl;

    try {
      const params = new URLSearchParams({
        q: q,
        format: 'json',
        limit: String(SEARCH.limit),
        addressdetails: '1',
        'accept-language': 'en,hi',
      });
      const res = await fetch(SEARCH.url + '?' + params.toString(), {
        signal: ctrl.signal,
        headers: { 'Accept': 'application/json' },
      });
      if (token !== SEARCH.token) return; // stale response
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (token !== SEARCH.token) return;
      SEARCH.results = Array.isArray(data) ? data : [];
      SEARCH.index = SEARCH.results.length ? 0 : -1;
      renderSearchResults(SEARCH.results, q);
      setActiveSearchItem(SEARCH.index);
    } catch (e) {
      if (token !== SEARCH.token) return;
      if (e && e.name === 'AbortError') return;
      SEARCH.results = [];
      renderSearchResults(null, q, 'error');
    }
  }

  function pickSearchResult(i) {
    const item = SEARCH.results[i];
    if (!item) return false;
    const lat = num(item.lat);
    const lon = num(item.lon);
    if (lat === null || lon === null || !isFinite(lat) || !isFinite(lon)) return false;

    const zoom = zoomForResult(item);
    const label = searchShortLabel(item);

    if (map) {
      map.flyTo([lat, lon], zoom, { duration: 0.9 });
    }
    selectPoint(lat, lon, label, { skipMarker: false });

    const input = $('searchInput');
    if (input) input.value = label;
    const clearBtn = $('searchClear');
    if (clearBtn) clearBtn.hidden = false;
    closeSearchDropdown(true);
    setStatus('Showing ' + label + ' — RF / rainfall / weather queried for selected point', 'ok');
    return true;
  }

  function wireSearch() {
    const input = $('searchInput');
    if (!input) return;

    on(input, 'input', (e) => {
      const q = e.target.value.trim();
      const clearBtn = $('searchClear');
      if (clearBtn) clearBtn.hidden = q.length === 0;
      clearTimeout(SEARCH.timer);
      if (q.length < SEARCH.minLen) {
        closeSearchDropdown(true);
        return;
      }
      SEARCH.timer = setTimeout(() => runSearch(q), SEARCH.debounceMs);
    });

    on(input, 'keydown', (e) => {
      const items = SEARCH.results;
      if (e.key === 'ArrowDown') {
        if (!items.length) return;
        e.preventDefault();
        SEARCH.index = Math.min(items.length - 1, (SEARCH.index < 0 ? 0 : SEARCH.index + 1));
        setActiveSearchItem(SEARCH.index);
        return;
      }
      if (e.key === 'ArrowUp') {
        if (!items.length) return;
        e.preventDefault();
        SEARCH.index = Math.max(0, (SEARCH.index < 0 ? 0 : SEARCH.index - 1));
        setActiveSearchItem(SEARCH.index);
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        const idx = SEARCH.index >= 0 ? SEARCH.index : 0;
        if (items.length) pickSearchResult(idx);
        return;
      }
      if (e.key === 'Escape') {
        closeSearchDropdown(true);
        input.blur();
        return;
      }
    });

    on(input, 'focus', () => {
      if (SEARCH.results.length) { renderSearchResults(SEARCH.results, input.value); setActiveSearchItem(SEARCH.index); }
    });

    on(input, 'blur', () => {
      /* small delay so click on a result lands before dropdown closes */
      setTimeout(() => { if (document.activeElement !== input) closeSearchDropdown(false); }, 160);
    });

    const dropdown = $('searchDropdown');
    if (dropdown) {
      on(dropdown, 'pointerdown', (e) => {
        const btn = e.target.closest('.search-item');
        if (!btn) return;
        e.preventDefault();
        const idx = parseInt(btn.getAttribute('data-index'), 10);
        if (!isNaN(idx)) pickSearchResult(idx);
      });
    }

    const clearBtn = $('searchClear');
    if (clearBtn) {
      on(clearBtn, 'click', () => {
        input.value = '';
        clearBtn.hidden = true;
        closeSearchDropdown(true);
        updateCoords(DEFAULT_POINT.lat, DEFAULT_POINT.lon);
        input.focus();
        selectPoint(DEFAULT_POINT.lat, DEFAULT_POINT.lon, DEFAULT_POINT.label);
      });
    }

    /* global dismiss on outside click */
    on(document, 'pointerdown', (e) => {
      const wrap = $('searchWrap');
      if (wrap && !wrap.contains(e.target)) closeSearchDropdown(false);
    });
  }

  /* ── status bar ───────────────────────────────────────────────────────── */
  function updateCoords(lat, lon) {
    const ns = lat >= 0 ? 'N' : 'S';
    const ew = lon >= 0 ? 'E' : 'W';
    $('sbCoords').textContent =
      Math.abs(lat).toFixed(5) + '°' + ns + '  ' + Math.abs(lon).toFixed(5) + '°' + ew;
  }

  let moveRaf = null;
  function onMouseMove(e) {
    if (moveRaf) return;
    moveRaf = requestAnimationFrame(() => {
      moveRaf = null;
      updateCoords(e.latlng.lat, e.latlng.lng);
    });
  }

  function updateScale() {
    if (!map) return;
    const lat = map.getCenter().lat;
    const zoom = map.getZoom();
    const mpp = 156543.03392 * Math.cos((lat * Math.PI) / 180) / Math.pow(2, zoom);
    const denom = mpp / 0.0002645833; // ~96 dpi
    const exp = Math.floor(Math.log10(denom));
    const rounded = Math.round(denom / Math.pow(10, exp - 1)) * Math.pow(10, exp - 1);
    $('sbScale').textContent = '1 : ' + Math.round(rounded).toLocaleString('en-US');
    $('sbZoom').textContent = 'z' + zoom;
  }

  /* ── susceptibility overlay ───────────────────────────────────────────── */
  async function loadSusceptibility() {
    try {
      const overlayUrl = state.model === 'india'
        ? '/susceptibility/india_overlay.json'
        : '/susceptibility/overlay.json';
      const meta = await getJSON(overlayUrl);
      if (!meta || !meta.bounds) throw new Error('overlay bounds missing');
      susOverlay = L.imageOverlay(meta.image || '/susceptibility/overlay.png', meta.bounds, {
        opacity: state.overlayOpacity,
        interactive: false,
        className: 'susceptibility-overlay',
        alt: 'Experimental RF v3 terrain susceptibility overlay'
      });
      if (state.overlayVisible) susOverlay.addTo(map);
      if (meta.threshold !== undefined && $('scrThreshold')) {
        $('scrThreshold').textContent = Number(meta.threshold).toFixed(2);
      }
    } catch (e) {
      toast('Susceptibility overlay unavailable: ' + e.message, 'err');
      const box = $('tglSusceptibility');
      if (box) box.checked = false;
      state.overlayVisible = false;
    }
  }

  /* ── inventory ────────────────────────────────────────────────────────── */
  async function loadInventory() {
    try {
      const gj = await getJSON('/inventory', 30000);
      inventoryLayer = L.geoJSON(gj, {
        style: () => ({
          color: '#38bdf8',
          weight: 1.6,
          opacity: 0.95,
          fillColor: '#60a5fa',
          fillOpacity: 0.18
        }),
        onEachFeature: (feature, layer) => {
          layer.bindPopup(inventoryPopup(feature.properties || {}), { maxWidth: 280 });
          layer.on('mouseover', () => layer.setStyle({ weight: 2.2, fillOpacity: 0.32, color: '#7dd3fc' }));
          layer.on('mouseout', () => inventoryLayer.resetStyle(layer));
        }
      });
      if (state.inventoryVisible) inventoryLayer.addTo(map);
      const row = $('tglInventory') && $('tglInventory').closest('.switch-row');
      if (row) {
        const subEl = row.querySelector('.sw-sub');
        if (subEl) subEl.textContent = (gj.features ? gj.features.length : 0) + ' mapped polygons · 1999 event';
      }
    } catch (e) {
      toast('Landslide inventory unavailable: ' + e.message, 'err');
      const box = $('tglInventory');
      if (box) box.checked = false;
      state.inventoryVisible = false;
    }
  }

  function inventoryPopup(props) {
    const rows = [
      ['ID', props.ID],
      ['Area', props.Area != null ? Number(props.Area).toLocaleString('en-US') + ' m²' : null],
      ['Lithology', props.Lithology],
      ['LULC', props.LULC],
      ['Triggered by', props.Triggrd_by],
      ['Near to', props.Near_to],
      ['Fault', props.Fault]
    ].filter((r) => r[1] !== null && r[1] !== undefined && r[1] !== '');

    return '<div class="popup-title">Landslide inventory polygon</div>' +
      '<div class="popup-coords">Mapped feature' + (props.ID != null ? ' · ID ' + esc(props.ID) : '') + '</div>' +
      rows.map((r) => '<div class="popup-row popup-attrs"><span class="k">' + esc(r[0]) + '</span><span class="v">' + esc(r[1]) + '</span></div>').join('') +
      '<div class="popup-note">Inventory of the 1999 Mw 6.6 event. A historical record — not a current hazard or warning.</div>';
  }

  /* ── point selection ──────────────────────────────────────────────────── */
  let selectedMarker = null;

  function scheduleSelection(lat, lon, label) {
    clearTimeout(state.clickTimer);
    state.clickTimer = setTimeout(() => selectPoint(lat, lon, label), 220);
  }

  function selectPoint(lat, lon, label, opts) {
    opts = opts || {};
    state.selected = { lat: lat, lon: lon, label: label };
    $('wxPlaceLabel').textContent = label;
    updateCoords(lat, lon);

    if (!opts.skipMarker) {
      pointLayer.clearLayers();
      selectedMarker = L.marker([lat, lon], {
        icon: L.divIcon({ className: 'point-marker', html: '<div class="point-pin"></div>', iconSize: [14, 14], iconAnchor: [7, 7] }),
        keyboard: false,
        zIndexOffset: 500
      }).addTo(pointLayer);
      selectedMarker.bindPopup(popupSkeleton(lat, lon), { maxWidth: 300 });
      selectedMarker.on('popupopen', refreshPointPopup);
    } else {
      pointLayer.clearLayers();
      selectedMarker = null;
    }

    state.lastRf = null;
    state.lastScreening = null;

    loadRf(lat, lon);
    loadScreening(lat, lon);
    loadWeather(lat, lon, label);

    setStatus('Querying terrain, rainfall and weather…', 'warn');
  }

  function popupSkeleton(lat, lon) {
    return '<div class="popup-title">Selected point</div>' +
      '<div class="popup-coords">' + lat.toFixed(5) + '°N, ' + lon.toFixed(5) + '°E</div>' +
      '<div class="wx-msg">Querying terrain and screening…</div>';
  }

  function terrainCategory(prob, threshold) {
    const p = num(prob);
    const tHi = (num(threshold) !== null ? num(threshold) : 0.38);
    const tMed = Math.min(0.15, tHi * 0.4);   // keep a proportional medium band
    const cat = { label: 'LOW', color: '#22c55e', cls: 'cat-low' };
    if (p === null || p === undefined) {
      return { label: 'n/a', color: '#94a3b8', cls: '' };
    }
    if (p >= tHi) { cat.label = 'HIGH'; cat.color = '#ef4444'; cat.cls = 'cat-high'; }
    else if (p >= tMed) { cat.label = 'MEDIUM'; cat.color = '#f1c40f'; cat.cls = 'cat-med'; }
    else { cat.label = 'LOW'; cat.color = '#22c55e'; cat.cls = 'cat-low'; }
    cat.score = p;
    return cat;
  }

  function terrainInterpretation(cat) {
    if (cat.label === 'HIGH') {
      return 'High relative terrain susceptibility for this point (RF v3 experimental score >= 0.38, frozen HIGH threshold). Experimental model output; not a landslide prediction or safety determination; no operational warning is implied.';
    }
    if (cat.label === 'MEDIUM') {
      return 'Moderate relative terrain susceptibility for this point (RF v3 experimental score in the 0.15-0.38 experimental band). Experimental mid band; not a prediction or safety determination.';
    }
    if (cat.label === 'LOW') {
      return 'Low relative terrain susceptibility for this point (RF v3 experimental score < 0.15). Low model-driven score; not a hazard or safety determination.';
    }
    return 'Terrain susceptibility could not be assessed for this point. No prediction fabricated.';
  }

  function refreshPointPopup() {
    if (!selectedMarker) return;
    const lat = state.selected.lat, lon = state.selected.lon;
    let html = '<div class="popup-title">Selected point</div>' +
      '<div class="popup-coords">' + lat.toFixed(5) + '°N, ' + lon.toFixed(5) + '°E</div>';

    const rf = state.lastRf;
    const cat = terrainCategory(
      rf && rf.status === 'inference_complete' ? rf.susceptibility_probability : null,
      rf && rf.frozen_threshold !== undefined ? rf.frozen_threshold : undefined
    );
    if (rf && rf.status === 'inference_complete') {
      html += '<div class="popup-cat-row">' +
        '<span class="k">Terrain susceptibility</span>' +
        '<span class="popup-cat ' + cat.cls + '">' + cat.label + '</span>' +
        '</div>';
      html += '<div class="popup-row"><span class="k">RF v3 score</span><span class="v">' +
        num(rf.susceptibility_probability).toFixed(4) + '</span></div>';
      const frac = Math.min(100, cat.score * 100);
      html += '<div class="bar"><span style="width:' + frac + '%;background:linear-gradient(90deg,#22c55e,#ef4444)"></span><i class="bar-marker" style="left:' + frac + '%"></i></div>';
      html += '<div class="popup-note">' + terrainInterpretation(cat) + '</div>';
    } else if (rf && (rf.status === 'model_trained_out_of_coverage' || rf.status === 'outside_coverage' || rf.status === 'out_of_coverage')) {
      html += '<div class="popup-cat-row">' +
        '<span class="k">Terrain susceptibility</span>' +
        '<span class="popup-cat ' + (cat.cls || '') + '">outside coverage</span>' +
        '</div>';
      html += '<div class="popup-row"><span class="k">RF score</span><span class="v">—</span></div>';
      html += '<div class="popup-note">Clicked point is outside verified SRTM coverage — no prediction fabricated.</div>';
    } else if (rf) {
      html += '<div class="popup-cat-row">' +
        '<span class="k">Terrain susceptibility</span>' +
        '<span class="popup-cat ' + (cat.cls || '') + '">' + esc(rf.status || 'unavailable') + '</span>' +
        '</div>';
      html += '<div class="popup-row"><span class="k">RF v3 score</span><span class="v">—</span></div>';
      html += '<div class="popup-note">Terrain inference unavailable: ' + esc(rf.message || 'no result') + '. No prediction fabricated.</div>';
    } else {
      html += '<div class="popup-row"><span class="k">Terrain (RF)</span><span class="v">Querying…</span></div>';
    }

    const sc = state.lastScreening;
    if (sc && sc.combined) {
      const s = num(sc.combined.combined_screening_index);
      const n = num(sc.combined.rainfall_normalized);
      html += '<div class="popup-row"><span class="k">Rain N<sub>rain</sub></span><span class="v">' + (n === null ? 'n/a' : n.toFixed(4)) + '</span></div>';
      html += '<div class="popup-row"><span class="k">Screening S</span><span class="v">' + (s === null ? 'not computed' : s.toFixed(4)) + '</span></div>';
    }

    html += '<div class="popup-note">Experimental terrain susceptibility assessment; not a landslide prediction or safety determination.</div>';
    selectedMarker.setPopupContent(html);
  }

  /* ── RF v3 point inference ────────────────────────────────────────────── */
  async function loadRf(lat, lon) {
    const token = ++state.rfToken;
    try {
      const d = await getJSON('/rf/susceptibility?lat=' + lat.toFixed(5) + '&lon=' + lon.toFixed(5) + '&model=' + state.model);
      if (token !== state.rfToken) return;
      state.lastRf = d;
      refreshPointPopup();
    } catch (e) {
      if (token !== state.rfToken) return;
      state.lastRf = { status: 'request_failed', message: e.message };
      refreshPointPopup();
    }
  }

  /* ── rainfall screening ───────────────────────────────────────────────── */
  function scrSet(id, text, cls) {
    const el = $(id);
    if (!el) return;
    el.textContent = text;
    if (cls !== undefined) el.className = 'val ' + (cls || '');
  }

  function scrColor(n) {
    if (n === null || n === undefined) return '#94a3b8';
    if (n < 0.15) return '#22c55e';
    if (n < 0.40) return '#f1c40f';
    if (n < 0.70) return '#e67e22';
    return '#c0392b';
  }

  function fmtWin(w) {
    if (!w) return { text: '—', cls: 'na' };
    if (w.status === 'unavailable') return { text: 'unavailable (no data)', cls: 'na' };
    if (w.accumulation_mm === null || w.accumulation_mm === undefined) return { text: 'unavailable (no data)', cls: 'na' };
    let text = Number(w.accumulation_mm).toFixed(1) + ' mm · N=' + (num(w.normalized) === null ? 'n/a' : num(w.normalized).toFixed(4));
    if (!w.complete) { text += ' ⚠ partial'; return { text: text, cls: 'warn' }; }
    return { text: text, cls: '' };
  }

  async function loadScreening(lat, lon) {
    const token = ++state.screeningToken;
    $('scrLoading').hidden = false;
    $('scrError').hidden = true;
    $('scrBody').hidden = true;

    try {
      const d = await getJSON('/screening?lat=' + lat.toFixed(5) + '&lon=' + lon.toFixed(5) + '&model=' + state.model, 30000);
      if (token !== state.screeningToken) return;
      state.lastScreening = d;
      renderScreening(d);
      refreshPointPopup();
    } catch (e) {
      if (token !== state.screeningToken) return;
      $('scrLoading').hidden = true;
      $('scrError').hidden = false;
      $('scrError').textContent = 'Screening unavailable: ' + e.message + ' — no rainfall is fabricated or zero-filled.';
      setStatus('Screening request failed', 'err');
    }
  }

  function renderScreening(d) {
    $('scrLoading').hidden = true;
    $('scrError').hidden = true;
    $('scrBody').hidden = false;

    const t = d.terrain || {};
    const rain = d.rainfall || {};
    const comb = d.combined || {};
    const obs = rain.observed || {};
    const fct = rain.forecast || {};

    /* terrain */
    const tp = num(t.rf_v3_probability);
    const cat = terrainCategory(tp, t.frozen_threshold);
    $('scrTerrain').textContent = tp === null ? 'unavailable' : (tp * 100).toFixed(1) + '%';
    const clsEl = $('scrTerrainClass');
    clsEl.textContent = cat.label;
    clsEl.className = 'tag class ' + (cat.label === 'HIGH' ? 'tag-warn' : cat.label === 'MEDIUM' ? 'tag-info' : 'tag-muted');
    $('scrTerrainBar').style.width = (tp === null ? 0 : Math.min(100, tp * 100)) + '%';
    $('scrTerrainBar').style.background = 'linear-gradient(90deg,#22c55e,#ef4444)';
    $('scrThreshold').textContent = t.frozen_threshold != null ? Number(t.frozen_threshold).toFixed(2) : '0.38';

    /* observed rainfall */
    const ow = obs.windows || {};
    [['1h', 'scrObs1h'], ['3h', 'scrObs3h'], ['24h', 'scrObs24h'], ['72h', 'scrObs72h'], ['7d', 'scrObs7d']].forEach((pair) => {
      const w = ow[pair[0]];
      const out = fmtWin(w);
      scrSet(pair[1], out.text, out.cls);
      if (w && w.reference_mm != null) $('scrObs' + pair[0] + 'R').textContent = w.reference_mm;
    });

    /* indicator */
    const nr = num(obs.normalized_indicator);
    scrSet('scrNRain', nr === null ? 'n/a — no complete window' : nr.toFixed(4), nr === null ? 'na' : '');
    const nbar = $('scrNRainBar');
    nbar.style.width = (nr === null ? 0 : Math.min(100, nr * 100)) + '%';
    nbar.style.background = scrColor(nr);

    let note = 'Trailing windows ending at the last complete hour (current partial hour excluded). ';
    if (obs.anchor_hour_ist) note += 'Anchor ' + obs.anchor_hour_ist + ' IST. ';
    if (obs.partial_windows && obs.partial_windows.length) note += 'Partial windows excluded: ' + obs.partial_windows.join(', ') + '. ';
    if (obs.unavailable_windows && obs.unavailable_windows.length) note += 'Unavailable: ' + obs.unavailable_windows.join(', ') + '. ';
    if (rain.status === 'unavailable') note += 'Rainfall unavailable — the combined index is not computed.';
    $('scrRainNote').textContent = note;

    /* forecast */
    const fw = fct.windows || {};
    [['1h', 'scrFct1h'], ['3h', 'scrFct3h'], ['24h', 'scrFct24h']].forEach((pair) => {
      const w = fw[pair[0]];
      const out = fmtWin(w);
      scrSet(pair[1], out.text, out.cls);
      if (w && w.reference_mm != null) $('scrFct' + pair[0] + 'R').textContent = w.reference_mm;
    });
    const nf = num(fct.normalized_indicator);
    scrSet('scrNFct', nf === null ? 'n/a' : nf.toFixed(4), nf === null ? 'na' : '');

    /* combined */
    $('scrAlpha').textContent = comb.alpha != null ? Number(comb.alpha).toFixed(1) : '—';
    const s = num(comb.combined_screening_index);
    $('scrCombined').textContent = s === null ? (comb.reason ? 'not computed' : '—') : s.toFixed(4);
    const cbar = $('scrCombinedBar');
    cbar.style.width = (s === null ? 0 : Math.min(100, s * 100)) + '%';
    cbar.style.background = 'linear-gradient(90deg,' + scrColor(s) + ', #ef4444)';
    $('scrFormula').textContent = 'S = clamp₀₁( P_terrain × (1 + α × N_rain) ) · basis: ' +
      (comb.rainfall_basis || 'observed') + (comb.reason ? ' · ' + comb.reason : '');

    if (rain.status === 'unavailable') {
      $('scrError').hidden = false;
      $('scrError').textContent = 'Rainfall unavailable: ' + (rain.error || 'no usable Open-Meteo data') +
        ' — missing rainfall is never replaced with 0.';
    }

    /* disclaimer + source (verbatim lines joined) */
    if (Array.isArray(d.disclaimer) && d.disclaimer.length) {
      $('scrDisclaimer').textContent = d.disclaimer.join(' ');
    }
    const bits = [];
    if (rain.source) bits.push(rain.source);
    if (rain.fetched_at_ist) bits.push('fetched ' + rain.fetched_at_ist);
    if (rain.anchor_hour_ist) bits.push('anchor ' + rain.anchor_hour_ist);
    bits.push(rain.from_cache ? '5-min cache' : 'live request');
    $('scrSource').textContent = bits.join(' · ');
  }

  /* ── weather ──────────────────────────────────────────────────────────── */
  function wxIcon(code, isDay) {
    const c = num(code);
    if (c === null) return '◌';
    if (c === 0) return isDay === 0 ? '🌙' : '☀️';
    if (c === 1) return isDay === 0 ? '🌙' : '🌤️';
    if (c === 2) return isDay === 0 ? '☁️' : '⛅';
    if (c === 3) return '☁️';
    if (c === 45 || c === 48) return '🌫️';
    if (c >= 51 && c <= 57) return '🌦️';
    if (c >= 61 && c <= 67) return '🌧️';
    if (c >= 71 && c <= 77) return '🌨️';
    if (c >= 80 && c <= 82) return '🌧️';
    if (c === 85 || c === 86) return '🌨️';
    if (c >= 95) return '⛈️';
    return '◌';
  }

  /* WMO weather interpretation code → short plain-text label (Open-Meteo uses WMO codes) */
  function wxCondition(code) {
    const c = num(code);
    if (c === null || c === undefined) return 'Unknown';
    if (c === 0) return 'Clear sky';
    if (c === 1) return 'Mainly clear';
    if (c === 2) return 'Partly cloudy';
    if (c === 3) return 'Overcast';
    if (c === 45 || c === 48) return 'Fog';
    if (c >= 51 && c <= 55) return 'Drizzle';
    if (c === 56 || c === 57) return 'Freezing drizzle';
    if (c >= 61 && c <= 65) return 'Rain';
    if (c === 66 || c === 67) return 'Freezing rain';
    if (c >= 71 && c <= 77) return 'Snowfall';
    if (c >= 80 && c <= 82) return 'Rain showers';
    if (c === 85 || c === 86) return 'Snow showers';
    if (c >= 95) return 'Thunderstorm';
    return 'Unknown';
  }

  function showWeatherLoading() {
    $('wxLoading').hidden = false;
    $('wxContent').hidden = true;
    $('wxError').hidden = true;
    $('wxUpdated').textContent = 'updating…';
  }

  function showWeatherError(msg) {
    $('wxLoading').hidden = true;
    /* Staleness policy: never blank out values the user can already see.
       If a previous payload populated the panel, keep it visible and flag it stale.
       Only hide the panel when there is no previous data at all. */
    const hadData = !!$('wxContent').dataset.filled;
    if (!hadData) $('wxContent').hidden = true;
    $('wxError').hidden = false;
    $('wxErrorMsg').textContent = hadData
      ? 'STALE — live update failed: ' + msg + '. Values shown below are the last successful fetch (not live).'
      : 'Weather unavailable: ' + msg;
    const headSrc = $('wxHeadSrc');
    if (headSrc) headSrc.textContent = 'STALE';
    $('wxUpdated').textContent = 'fetch failed';
    setStatus('Weather unavailable — showing stale data', 'err');
  }

  async function loadWeather(lat, lon, label) {
    const token = ++state.weatherToken;
    showWeatherLoading();
    try {
      const d = await getJSON('/weather?lat=' + lat.toFixed(5) + '&lon=' + lon.toFixed(5), 30000);
      if (token !== state.weatherToken) return;
      if (d && d.error) throw new Error(d.error);
      renderWeather(d, label);
      setStatus('Open-Meteo live · RF susceptibility · experimental screening', 'ok');
    } catch (e) {
      if (token !== state.weatherToken) return;
      showWeatherError('Weather unavailable: ' + e.message);
    }
  }

  function renderWeather(d, label) {
    const c = d.current || {};

    $('wxLoading').hidden = true;
    $('wxContent').hidden = false;
    $('wxContent').dataset.filled = '1';
    $('wxError').hidden = true;
    const headSrcOk = $('wxHeadSrc');
    if (headSrcOk) headSrcOk.textContent = 'Open-Meteo';
    if (label) $('wxPlaceLabel').textContent = label;
    const coords = (d.coordinates && typeof d.coordinates.latitude === 'number')
      ? d.coordinates.latitude.toFixed(2) + '°N, ' + d.coordinates.longitude.toFixed(2) + '°E'
      : null;
    if (coords && !label) $('wxPlaceLabel').textContent = coords;
    $('wxUpdated').textContent = d.fetched_at_ist
      ? 'updated ' + String(d.fetched_at_ist).replace(/^\d{4}-\d{2}-\d{2}\s+/, '') + (d.from_cache ? ' · cache' : '')
      : '—';

    const tc = num(c.temperature_2m);
    $('wxTemp').textContent = tc === null ? '—' : Math.round(tc);
    $('wxCondition').textContent = wxCondition(c.weather_code);
    $('wxIcon').textContent = wxIcon(c.weather_code, c.is_day);
    const at = num(c.apparent_temperature);
    $('wxFeels').textContent = 'Feels like ' + (at === null ? '—' : Math.round(at) + '°C');

    $('wxHumidity').textContent = num(c.relative_humidity_2m) === null ? '—' : Math.round(num(c.relative_humidity_2m));
    $('wxWind').textContent = num(c.wind_speed_10m) === null ? '—' : Math.round(num(c.wind_speed_10m));
    $('wxPrecip').textContent = num(c.precipitation) === null ? '—' : num(c.precipitation).toFixed(1);
    $('wxGust').textContent = num(c.wind_gusts_10m) === null ? '—' : Math.round(num(c.wind_gusts_10m));

    renderHourly(d);
    renderDaily(d);
  }

  function renderHourly(d) {
    const h = d.hourly || {};
    const times = h.time || [];
    const box = $('wxHourly');
    if (!times.length) { box.innerHTML = '<div class="wx-msg">Hourly forecast unavailable.</div>'; return; }

    const pad = (n) => String(n).padStart(2, '0');
    /* Open-Meteo hourly times are Asia/Kolkata local; anchor to IST, not the browser timezone */
    const istNow = new Date(Date.now() + 5.5 * 3600 * 1000);
    const nowLocal = istNow.getUTCFullYear() + '-' + pad(istNow.getUTCMonth() + 1) + '-' + pad(istNow.getUTCDate()) +
      'T' + pad(istNow.getUTCHours()) + ':00';

    let start = times.findIndex((t) => t >= nowLocal);
    if (start < 0) start = 0;

    let html = '';
    for (let i = start; i < Math.min(start + 12, times.length); i++) {
      const t = times[i];
      const temp = num((h.temperature_2m || [])[i]);
      const prob = num((h.precipitation_probability || [])[i]);
      const code = (h.weather_code || [])[i];
      const isDay = (h.is_day || [])[i];
      const precip = num((h.precipitation || [])[i]);
      const hh = t.slice(11, 16);
      html += '<div class="wx-hour" title="' + esc(t) + '">' +
        '<div class="h">' + esc(hh) + '</div>' +
        '<div class="i" aria-hidden="true">' + wxIcon(code, isDay) + '</div>' +
        '<div class="t">' + (temp === null ? '—' : Math.round(temp) + '°') + '</div>' +
        '<div class="p">' + (prob === null ? '' : Math.round(prob) + '%') + '</div>' +
        '</div>';
    }
    box.innerHTML = html;
  }

  function renderDaily(d) {
    const dy = d.daily || {};
    const times = dy.time || [];
    const box = $('wxDaily');
    if (!times.length) { box.innerHTML = '<div class="wx-msg">Daily forecast unavailable.</div>'; return; }

    const mins = (dy.temperature_2m_min || []).map(num);
    const maxs = (dy.temperature_2m_max || []).map(num);
    const valid = mins.concat(maxs).filter((v) => v !== null);
    const gMin = valid.length ? Math.min.apply(null, valid) : 0;
    const gMax = valid.length ? Math.max.apply(null, valid) : 1;
    const span = Math.max(1, gMax - gMin);

    let html = '';
    const n = Math.min(7, times.length);
    for (let i = 0; i < n; i++) {
      const t = times[i];
      const mn = mins[i], mx = maxs[i];
      const code = (dy.weather_code || [])[i];
      const prob = num((dy.precipitation_probability_max || [])[i]);
      const rain = num((dy.precipitation_sum || [])[i]);
      let day;
      try {
        const dt = new Date(t + 'T00:00:00Z');
        day = i === 0 ? 'Today' : dt.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' });
      } catch (e) { day = t.slice(5); }

      const left = mn === null ? 0 : ((mn - gMin) / span) * 100;
      const width = (mn === null || mx === null) ? 0 : Math.max(4, ((mx - mn) / span) * 100);

      html += '<div class="wx-day" title="' + esc(wxCondition(code)) + '">' +
        '<span class="d">' + esc(day) + '</span>' +
        '<span class="i" aria-hidden="true">' + wxIcon(code, 1) + '</span>' +
        '<span class="bar"><span style="left:' + left.toFixed(1) + '%;width:' + width.toFixed(1) + '%"></span></span>' +
        '<span class="r"><b>' + (mx === null ? '—' : Math.round(mx) + '°') + '</b> / ' + (mn === null ? '—' : Math.round(mn) + '°') +
        (prob === null ? '' : ' · ' + Math.round(prob) + '%') +
        (rain === null ? '' : ' · ' + rain.toFixed(1) + 'mm') + '</span>' +
        '</div>';
    }
    box.innerHTML = html;
  }

  /* ── my location ──────────────────────────────────────────────────────── */
  function locateUser() {
    if (!navigator.geolocation) {
      toast('Geolocation is not supported by this browser.', 'err');
      return;
    }
    const btn = $('btnLocate');
    const txt = btn ? btn.querySelector('.txt') : null;
    const original = txt ? txt.textContent : '';
    if (btn) btn.disabled = true;
    if (txt) txt.textContent = 'Locating…';

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (btn) btn.disabled = false;
        if (txt) txt.textContent = original;
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        const acc = Math.max(1, Math.round(pos.coords.accuracy || 0));
        state.locationAccuracy = acc;

        locationLayer.clearLayers();
        const ring = L.circle([lat, lon], {
          radius: acc, color: '#2dd4bf', weight: 1, opacity: 0.9,
          fillColor: '#2dd4bf', fillOpacity: 0.08, dashArray: '4 4', interactive: false
        }).addTo(locationLayer);
        const marker = L.marker([lat, lon], {
          icon: L.divIcon({ className: 'loc-marker', html: '<div class="loc-pin"></div>', iconSize: [16, 16], iconAnchor: [8, 8] }),
          zIndexOffset: 700, keyboard: false
        }).addTo(locationLayer);
        marker.bindPopup('<div class="popup-title">My location</div>' +
          '<div class="popup-coords">' + lat.toFixed(5) + '°N, ' + lon.toFixed(5) + '°E</div>' +
          '<div class="popup-row"><span class="k">GPS accuracy</span><span class="v">±' + acc + ' m</span></div>' +
          '<div class="popup-note">Position from your browser. Not stored or sent anywhere except the map APIs for the point you request.</div>');

        map.flyTo([lat, lon], Math.max(map.getZoom(), 13), { duration: 0.9 });
        selectPoint(lat, lon, 'My location (±' + acc + ' m)', { skipMarker: true });
        setStatus('GPS fix ±' + acc + ' m · Open-Meteo live', 'ok');
        toast('Centered on your location (±' + acc + ' m). Outside Chamoli, terrain inference reports "out of coverage" honestly.', '');
      },
      (err) => {
        if (btn) btn.disabled = false;
        if (txt) txt.textContent = original;
        let msg = 'Location unavailable.';
        if (err && err.code === 1) msg = 'Location permission denied. You can still explore the map and the default Chamoli point.';
        else if (err && err.code === 2) msg = 'Location unavailable: no GPS fix from this device.';
        else if (err && err.code === 3) msg = 'Location request timed out. Try again.';
        toast(msg, 'err');
        setStatus('Location unavailable', 'warn');
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 }
    );
  }

  /* ── fullscreen ───────────────────────────────────────────────────────── */
  function toggleFullscreen() {
    const el = document.documentElement;
    const isFs = document.fullscreenElement || document.webkitFullscreenElement;
    if (!isFs) {
      const req = el.requestFullscreen || el.webkitRequestFullscreen;
      if (!req) { toast('Fullscreen is not supported in this browser.', 'err'); return; }
      Promise.resolve(req.call(el)).catch(() => toast('Fullscreen could not be enabled.', 'err'));
    } else {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) Promise.resolve(exit.call(document)).catch(() => {});
    }
  }

  function syncFsIcon() {
    const isFs = !!(document.fullscreenElement || document.webkitFullscreenElement);
    const icon = $('fsIcon');
    if (icon) icon.querySelector('use').setAttribute('href', isFs ? '#i-shrink' : '#i-expand');
    if (map) {
      map.invalidateSize();
      setTimeout(() => map.invalidateSize(), 80);
      setTimeout(() => map.invalidateSize(), 250);
    }
  }

  /* ── panels ───────────────────────────────────────────────────────────── */
  function wirePanels() {
    document.querySelectorAll('[data-collapse]').forEach((btn) => {
      on(btn, 'click', (e) => {
        e.stopPropagation();
        const panel = $(btn.dataset.collapse);
        if (panel) panel.classList.toggle('collapsed');
      });
    });

    document.querySelectorAll('[data-bm]').forEach((btn) => {
      on(btn, 'click', () => setBasemap(btn.dataset.bm));
    });

    document.querySelectorAll('[data-wxtab]').forEach((btn) => {
      on(btn, 'click', () => setWxTab(btn.dataset.wxtab));
    });

    on($('tglSusceptibility'), 'change', (e) => {
      state.overlayVisible = e.target.checked;
      if (!susOverlay) return;
      if (state.overlayVisible) susOverlay.addTo(map);
      else map.removeLayer(susOverlay);
    });

    on($('opacityRange'), 'input', (e) => {
      const v = Math.max(0, Math.min(100, Number(e.target.value)));
      state.overlayOpacity = v / 100;
      $('opacityOut').textContent = v + '%';
      if (susOverlay) susOverlay.setOpacity(state.overlayOpacity);
    });

    on($('tglInventory'), 'change', (e) => {
      state.inventoryVisible = e.target.checked;
      if (!inventoryLayer) return;
      if (state.inventoryVisible) inventoryLayer.addTo(map);
      else map.removeLayer(inventoryLayer);
    });

    on($('tglScreening'), 'change', (e) => {
      const panel = $('panelScreening');
      if (panel) panel.style.display = e.target.checked ? '' : 'none';
    });

    on($('btnRetryWeather'), 'click', () => {
      loadWeather(state.selected.lat, state.selected.lon, state.selected.label);
    });

    /* mobile sheets — toggled only by the mobilebar buttons (deliberate);
       a map tap never closes them */
    document.querySelectorAll('[data-sheet]').forEach((btn) => {
      on(btn, 'click', () => toggleSheet(btn.dataset.sheet));
    });

    on(window, 'keydown', (e) => { if (e.key === 'Escape') closeSheets(); });
  }

  function setWxTab(which) {
    document.querySelectorAll('.wx-tab').forEach((b) => {
      const active = b.dataset.wxtab === which;
      b.classList.toggle('active', active);
      b.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    const hourly = $('wxHourlyWrap');
    const daily = $('wxDailyWrap');
    if (hourly) hourly.hidden = which !== 'hourly';
    if (daily) daily.hidden = which !== 'daily';
  }

  function toggleSheet(which) {
    const layers = $('panelLayers');
    const info = $('rightStack');
    const openLayers = which === 'layers' && !layers.classList.contains('open');
    const openInfo = which === 'info' && !info.classList.contains('open');

    layers.classList.toggle('open', openLayers);
    info.classList.toggle('open', openInfo);
  }

  function closeSheets() {
    const layers = $('panelLayers');
    const info = $('rightStack');
    if (layers) layers.classList.remove('open');
    if (info) info.classList.remove('open');
  }

  /* ── boot ─────────────────────────────────────────────────────────────── */
  function init() {
    /* nav active state from page */
    const activePage = state.page === 'map' ? 'map' : 'dashboard';
    document.querySelectorAll('.navlink').forEach((a) => {
      a.classList.toggle('active', a.dataset.nav === activePage);
    });

    if (!window.L) {
      setStatus('Leaflet failed to load', 'err');
      toast('Map library failed to load. Check that Leaflet is being served.', 'err');
      return;
    }

    initMap();
    wirePanels();
    wireModelPicker();
    wireSearch();

    /* Layout settles after CSS/fonts — force Leaflet to remeasure */
    const invalidate = () => { if (map) map.invalidateSize(); };
    requestAnimationFrame(invalidate);
    if (typeof ResizeObserver !== 'undefined') { /* backup */ }
    window.addEventListener('load', invalidate);
    [50, 150, 300, 600, 1200].forEach((t) => setTimeout(invalidate, t));


    on($('btnLocate'), 'click', locateUser);
    on($('btnFullscreen'), 'click', toggleFullscreen);
    document.addEventListener('fullscreenchange', syncFsIcon);
    document.addEventListener('webkitfullscreenchange', syncFsIcon);

    /* Ensure Leaflet picks up the final container size after layout
       (fixes black/blank map on first paint) */
    const raf = () => { if (map) map.invalidateSize(); };
    requestAnimationFrame(raf);
    window.addEventListener('load', raf);
    setTimeout(raf, 250);
    setTimeout(raf, 1000);

    window.addEventListener('resize', () => {
      if (map) map.invalidateSize();
      if (!isMobile()) closeSheets();
    });

    /* map page starts with a cleaner map: layers panel collapsed */
    if (state.page === 'map') {
      const lp = $('panelLayers');
      if (lp) lp.classList.add('collapsed');
    }

    updateCoords(DEFAULT_POINT.lat, DEFAULT_POINT.lon);
    $('sbBasemap').textContent = BASEMAPS[state.basemapKey].label;

    window.TerraGuard = {
      map: map,
      state: state,
      selectPoint: selectPoint,
      setBasemap: setBasemap,
      get overlay() { return susOverlay; },
      get inventory() { return inventoryLayer; },
      get selectedMarker() { return selectedMarker; },
      categoryFor: terrainCategory
    };

    loadSusceptibility();
    loadInventory();
    selectPoint(DEFAULT_POINT.lat, DEFAULT_POINT.lon, DEFAULT_POINT.label);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();