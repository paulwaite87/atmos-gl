import { createFillLayer, cssToRgb } from './_webglfill.js';
import { timeline } from './timeline.js';

export function loadLayer(map, config, fullConfig = {}) {
    // Manages the per-hour pressure-label symbol layer; created on mount, torn down
    // on unmount, so labels appear/disappear in lockstep with the isobar lines.
    const labels = makePressureLabels(map, config);

    // --- GPU isobar LINES (unchanged): crisp, animated, cross-faded ---
    // Return the fill teardown; the label layer's mount/unmount is wired to the fill's
    // onMount/onUnmount, so tearing down the fill also removes the labels.
    return createFillLayer(map, {
        sectionKey: 'isobars',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: 950.0,
        vspan: 100.0,                          // 1050 - 950, matches backend encode
        opacity: 0.85,
        bicubic: true,                         // smooth contour paths at high zoom
        // 16-bit value decode is the default, giving ~0.0015 hPa precision so contour
        // lines aren't quantised. Rendered as a custom layer at SCREEN resolution, so
        // lines stay crisp at any zoom and line width is in true screen pixels.
        fragmentBody: `
            uniform float u_interval;          // hPa between isobars
            uniform float u_linewidth;         // line width in SCREEN px
            uniform vec3  u_linecolor;
            vec4 shade(float value, vec2 uv) {
                float f = value / u_interval;
                float distToLine = abs(fract(f + 0.5) - 0.5);   // f-units to nearest contour
                float aa = fwidth(f);                            // f-units per screen pixel
                if (aa <= 0.0) discard;
                float px = distToLine / aa;                      // pixels to nearest contour
                float halfW = max(u_linewidth, 0.5) * 0.5;
                float alpha = 1.0 - smoothstep(halfW - 0.5, halfW + 0.5, px);
                if (alpha <= 0.001) discard;
                return vec4(u_linecolor, alpha);
            }`,
        customUniforms: (cfg) => ({
            u_interval: Number(cfg.isobar_step) > 0 ? Number(cfg.isobar_step) : 4.0,
            u_linewidth: Number(cfg.linewidth) > 0 ? Number(cfg.linewidth) : 1.4,
            u_linecolor: cssToRgb(cfg.isobar_color),
        }),
        // Pressure LABELS: vector text from per-hour GeoJSON (positions harvested from
        // matplotlib clabel on the backend), since a fragment shader can't draw text.
        onMount: () => labels.mount(),
        onUnmount: () => labels.unmount(),
    });
}

function makePressureLabels(map, config) {
    const sourceId = 'isobars-labels-source';
    const layerId = 'isobars-labels-layer';
    const EMPTY = { type: 'FeatureCollection', features: [] };

    const labelUrl = (hour, bust) => {
        const base = config.outfile.replace(/\.png$/, '');
        const f = String(hour).padStart(3, '0');
        return `${window.MAP_UI}/${base}_f${f}_labels.geojson?t=${bust}`;
    };

    const color = config.isobar_color || '#ffffff';
    const fontSize = Number(config.label_fontsize) > 0 ? Number(config.label_fontsize) : 11;

    let unsub = null, lastHour = -1, lastBust = -1, inflight = null;

    const loadHour = async (snap) => {
        const token = `${snap.hour}:${snap.refreshEpoch}`;
        inflight = token;
        try {
            const res = await fetch(labelUrl(snap.hour, snap.refreshEpoch));
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const gj = await res.json();
            if (inflight !== token) return;          // superseded by a newer hour
            const src = map.getSource(sourceId);
            if (src) src.setData(gj);
        } catch (e) {
            if (inflight === token) {                // missing hour -> clear, don't break
                const src = map.getSource(sourceId);
                if (src) src.setData(EMPTY);
            }
        }
    };

    const onTimeline = (snap) => {
        if (snap.hour !== lastHour || snap.refreshEpoch !== lastBust) {
            lastHour = snap.hour; lastBust = snap.refreshEpoch;
            loadHour(snap);
        }
    };

    return {
        mount() {
            if (!map.getSource(sourceId)) map.addSource(sourceId, { type: 'geojson', data: EMPTY });
            if (!map.getLayer(layerId)) {
                map.addLayer({
                    id: layerId, type: 'symbol', source: sourceId,
                    layout: {
                        'text-field': ['get', 'label'],
                        'text-font': ['Open Sans Regular'],
                        'text-size': fontSize,
                        'text-allow-overlap': false,
                        'text-ignore-placement': false,
                        'text-padding': ['interpolate', ['linear'], ['zoom'], 2, 24, 5, 8, 9, 2],
                    },
                    paint: {
                        'text-color': color,
                        'text-halo-color': 'rgba(0,0,0,0.85)',
                        'text-halo-width': 1.4,
                    },
                });
            }
            lastHour = -1; lastBust = -1;            // force first load
            if (!unsub) unsub = timeline.subscribe(onTimeline);   // fires immediately
        },
        unmount() {
            if (unsub) { try { unsub(); } catch {} unsub = null; }
            if (map.getLayer(layerId)) map.removeLayer(layerId);
            if (map.getSource(sourceId)) map.removeSource(sourceId);
        },
    };
}