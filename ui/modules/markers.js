import { liveDataSync } from './_datasync.js';

// Static place-marker layer: a small dot + label per place, loaded from the
// hard-coded markers/markers.geojson. Labels are collision-managed and revealed
// in priority order (biggest/most-prominent places first), so the globe stays
// uncluttered at world zoom and fills in as you zoom -- no zoom threshold needed.
export function loadLayer(map, config) {
    const sourceId = 'markers-source';
    const dotLayerId = 'markers-dots';
    const labelLayerId = 'markers-labels';
    const dataUrl = `${window.MAP_UI}/api/markers/geojson`;

    const colorOf = (cfg) => cfg.marker_color || 'white';
    const sizeOf = (cfg) => Number(cfg.marker_fontsize) || 11;
    // Honour an optional per-feature `color` (e.g. the poles' LightBlue),
    // otherwise fall back to the configured marker_color.
    const colorExpr = (cfg) => ['coalesce', ['get', 'color'], colorOf(cfg)];

    const numberWithCommas = (x) => {
        // If x is null or undefined, default to 0 (or return an empty string "" if preferred)
        const num = x ?? 0;
        return new Intl.NumberFormat('en-US').format(num);
    };
    // ---- Weather popups -------------------------------------------------------
    // Both the markers AND their current weather come from /api/markers/geojson — the
    // backend markers task samples the GFS temperature/wind/humidity fields valid "now"
    // and stores them on each marker row, so the weather rides along in feature
    // properties (t, rh, ws, wd). Hovering a marker shows it; weather_popup gates display.
    let weatherEnabled = !!config.weather_popup;   // markers.weather_popup toggle (live)
    const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
    const compassOf = (deg) => COMPASS[Math.round(deg / 45) % 8];

    const popup = new maplibregl.Popup({
        closeButton: false, closeOnClick: false, offset: 12, maxWidth: '240px',
    });
    const row = (label, value) =>
        `<div style="display:flex;justify-content:space-between;gap:14px;">` +
        `<span style="color:#666;">${label}</span><strong>${value}</strong></div>`;
    const popupHtml = (props, w) => {
        const pop = numberWithCommas(props.pop)
        const country = props.country
            ? `<div style="color:#888;font-size:11px;margin-top:-2px;">${props.country}<br/>Pop: ${pop}</div>` : '';
        let body;
        if (w) {
            const parts = [];
            if (w.t !== undefined) parts.push(row('Temp', `${Number(w.t).toFixed(1)} &deg;C`));
            if (w.rh !== undefined) parts.push(row('Humidity', `${w.rh}%`));
            if (w.ws !== undefined) {
                const kmh = Math.round(w.ws * 3.6);
                const dir = (w.wd !== undefined) ? ` from ${compassOf(w.wd)}` : '';
                parts.push(row('Wind', `${kmh} km/h${dir}`));
            }
            body = parts.length ? parts.join('') : '<div style="color:#888;">No data</div>';
        } else {
            body = '<div style="color:#888;font-size:12px;">Weather data unavailable</div>';
        }
        return `<div style="font-family:sans-serif;font-size:12.5px;color:#111;padding:2px 4px;min-width:150px;">` +
            `<div style="font-size:14px;font-weight:700;">${props.name || 'Unknown'}</div>${country}` +
            `<hr style="border:0;border-top:1px solid #ddd;margin:6px 0;">${body}</div>`;
    };

    // Hover popups: show on mousemove over a marker (anchored to the marker, so it stays
    // put while you're on it and follows when you slide to an adjacent one), hide on leave.
    const onMarkerHover = (e) => {
        if (!weatherEnabled || !e.features || !e.features.length) return;
        map.getCanvas().style.cursor = 'pointer';
        const f = e.features[0];
        const coords = f.geometry.coordinates.slice();
        const p = f.properties || {};
        // Weather rides along in the feature properties (null where not sampled).
        const w = {};
        if (p.t != null) w.t = p.t;
        if (p.rh != null) w.rh = p.rh;
        if (p.ws != null) w.ws = p.ws;
        if (p.wd != null) w.wd = p.wd;
        popup.setLngLat(coords)
            .setHTML(popupHtml(p, Object.keys(w).length ? w : null))
            .addTo(map);
    };
    const onLeave = () => { map.getCanvas().style.cursor = ''; popup.remove(); };

    // Keep the marker layers pinned to the top of the stack. Layers enabled AFTER markers
    // (precipitation, etc.) would otherwise render above the small dots/labels, hiding
    // them so there's nothing to hover. styledata fires whenever the layer stack changes;
    // we only move when the labels aren't already topmost, so this can't loop (moving makes
    // labels topmost -> the guard is false on the re-entrant styledata).
    const ensureOnTop = () => {
        const layers = map.getStyle()?.layers;
        if (!layers || !layers.length) return;
        if (layers[layers.length - 1].id === labelLayerId) return;   // already on top
        if (map.getLayer(dotLayerId)) map.moveLayer(dotLayerId);      // dots to top
        if (map.getLayer(labelLayerId)) map.moveLayer(labelLayerId);  // labels above dots
    };


    const mount = async (cfg) => {
        if (map.getSource(sourceId)) return;                 // guard against races
        const res = await fetch(dataUrl, { cache: 'no-cache' });
        if (!res.ok) throw new Error(`markers HTTP ${res.status}`);
        const data = await res.json();

        if (!map.getStyle()?.glyphs) {
            console.warn('[markers] base style declares no "glyphs" URL, so text labels ' +
                'cannot render. Add a glyphs endpoint at map init to enable labels.');
        }

        map.addSource(sourceId, { type: 'geojson', data });

        // Per-place dots (marine features get no dot). Dots reveal in priority bands
        // as you zoom, roughly tracking the label reveal, so a 1000+ marker set
        // doesn't become a wall of dots at low zoom.
        const dotReveal = ['step', ['zoom'],
            ['case', ['>=', ['get', 'priority'], 88], 1, 0],   // z3-4: ~115 top world cities
            4, ['case', ['>=', ['get', 'priority'], 70], 1, 0],  // + majors/capitals (~700)
            5, ['case', ['>=', ['get', 'priority'], 55], 1, 0],  // (~1400)
            6, ['case', ['>=', ['get', 'priority'], 40], 1, 0],  // (~3800)
            7, ['case', ['>=', ['get', 'priority'], 25], 1, 0],  // general tail (~6600)
            9, 1,                                                // everything
        ];
        map.addLayer({
            id: dotLayerId, type: 'circle', source: sourceId,
            minzoom: 3,                                       // nothing renders below z3
            filter: ['!=', ['get', 'kind'], 'feature'],       // seas/straits get no dot
            paint: {
                'circle-radius': 2.5,
                'circle-color': colorExpr(cfg),
                'circle-opacity': dotReveal,
                'circle-stroke-color': 'rgba(0,0,0,0.65)',
                'circle-stroke-width': 0.6,
                'circle-stroke-opacity': dotReveal,
            },
        });

        // Labels: collision detection (text-allow-overlap:false) drops overlapping
        // labels; symbol-sort-key decides who wins. MapLibre places lower sort-keys
        // first and keeps them, so invert priority (100 = top -> lowest sort key).
        map.addLayer({
            id: labelLayerId, type: 'symbol', source: sourceId,
            minzoom: 3,                                       // nothing renders below z3
            layout: {
                'text-field': ['get', 'name'],
                'text-font': ['Open Sans Regular'],
                'text-size': sizeOf(cfg),
                'text-anchor': 'top',
                'text-offset': [0, 0.55],
                'text-allow-overlap': false,
                'text-optional': true,
                // Collision spacing shrinks as you zoom: large padding at z3 forces
                // heavy thinning (only top-priority labels survive), relaxing toward
                // the default so more reveal progressively as you zoom in.
                'text-padding': ['interpolate', ['linear'], ['zoom'],
                    3, 60, 5, 24, 7, 10, 10, 3, 13, 2],
                'symbol-sort-key': ['-', 100, ['coalesce', ['get', 'priority'], 0]],
            },
            paint: {
                'text-color': colorExpr(cfg),
                'text-halo-color': 'rgba(0,0,0,0.85)',
                'text-halo-width': 1.2,
            },
        });

        // Weather popups: load the precomputed data (when enabled) and make markers
        // clickable. Handlers are always bound but no-op unless weather_popup is on, so
        // the toggle can flip live via refresh() without a remount.
        weatherEnabled = !!cfg.weather_popup;
        for (const id of [dotLayerId, labelLayerId]) {
            map.on('mousemove', id, onMarkerHover);
            map.on('mouseleave', id, onLeave);
        }
        // Keep markers clickable/hoverable above later-added layers, now and on every
        // subsequent layer-stack change.
        ensureOnTop();
        map.on('styledata', ensureOnTop);
    };

    // Marker geometry is static, but the weather on each feature refreshes every backend
    // cycle — so re-pull the API and setData to pick up new conditions, then re-apply
    // styling. weather_popup can toggle live (closes any open popup when turned off).
    const refresh = async (cfg) => {
        weatherEnabled = !!cfg.weather_popup;
        if (!weatherEnabled) popup.remove();
        try {
            const res = await fetch(dataUrl, { cache: 'no-cache' });
            if (res.ok) {
                const src = map.getSource(sourceId);
                if (src) src.setData(await res.json());
            }
        } catch { /* keep existing data on a failed refresh */ }
        if (map.getLayer(dotLayerId))
            map.setPaintProperty(dotLayerId, 'circle-color', colorExpr(cfg));
        if (map.getLayer(labelLayerId)) {
            map.setPaintProperty(labelLayerId, 'text-color', colorExpr(cfg));
            map.setLayoutProperty(labelLayerId, 'text-size', sizeOf(cfg));
        }
    };

    const unmount = () => {
        map.off('styledata', ensureOnTop);
        for (const id of [dotLayerId, labelLayerId]) {
            map.off('mousemove', id, onMarkerHover);
            map.off('mouseleave', id, onLeave);
        }
        popup.remove();
        if (map.getLayer(labelLayerId)) map.removeLayer(labelLayerId);
        if (map.getLayer(dotLayerId)) map.removeLayer(dotLayerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, {
        sectionKey: 'markers', initialConfig: config,
        mount, refresh, unmount,
        refreshMs: 3600000,   // static file; config edits drive the refresh, not a timer
    });
}