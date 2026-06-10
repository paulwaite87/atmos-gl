import { liveDataSync } from './_datasync.js';

// Static place-marker layer: a small dot + label per place, loaded from the
// hard-coded markers/markers.geojson. Labels are collision-managed and revealed
// in priority order (biggest/most-prominent places first), so the globe stays
// uncluttered at world zoom and fills in as you zoom -- no zoom threshold needed.
export function loadLayer(map, config) {
    const sourceId = 'markers-source';
    const dotLayerId = 'markers-dots';
    const labelLayerId = 'markers-labels';
    const dataUrl = `${window.location.origin}/markers/markers.geojson`;

    const colorOf = (cfg) => cfg.marker_color || 'white';
    const sizeOf = (cfg) => Number(cfg.marker_fontsize) || 11;
    // Honour an optional per-feature `color` (e.g. the poles' LightBlue),
    // otherwise fall back to the configured marker_color.
    const colorExpr = (cfg) => ['coalesce', ['get', 'color'], colorOf(cfg)];

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

        // Dots first, sitting beneath the labels.
        map.addLayer({
            id: dotLayerId, type: 'circle', source: sourceId,
            minzoom: 3,                                       // nothing renders below z3
            paint: {
                'circle-radius': 2.5,
                'circle-color': colorExpr(cfg),
                'circle-stroke-color': 'rgba(0,0,0,0.65)',
                'circle-stroke-width': 0.6,
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
                    3, 45, 5, 15, 8, 4, 11, 2],
                'symbol-sort-key': ['-', 100, ['coalesce', ['get', 'priority'], 0]],
            },
            paint: {
                'text-color': colorExpr(cfg),
                'text-halo-color': 'rgba(0,0,0,0.85)',
                'text-halo-width': 1.2,
            },
        });
    };

    // The data file is static, so a config-only change just re-applies styling.
    const refresh = async (cfg) => {
        if (map.getLayer(dotLayerId))
            map.setPaintProperty(dotLayerId, 'circle-color', colorExpr(cfg));
        if (map.getLayer(labelLayerId)) {
            map.setPaintProperty(labelLayerId, 'text-color', colorExpr(cfg));
            map.setLayoutProperty(labelLayerId, 'text-size', sizeOf(cfg));
        }
    };

    const unmount = () => {
        if (map.getLayer(labelLayerId)) map.removeLayer(labelLayerId);
        if (map.getLayer(dotLayerId)) map.removeLayer(dotLayerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    liveDataSync(map, {
        sectionKey: 'markers', initialConfig: config,
        mount, refresh, unmount,
        refreshMs: 3600000,   // static file; config edits drive the refresh, not a timer
    });
}