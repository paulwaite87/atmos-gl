import { liveLayerSync } from './_refresh.js';
import { createParticleController } from './_windparticles.js';

/**
 * Waves layer = Web-Mercator heat tiles (significant wave height) + an animated swell
 * field drawn as short bars perpendicular to the wave direction (the windy.com look).
 *
 * The heat tiles are pre-rendered + published by the backend and fetched as {z}/{x}/{y}
 * (see routes/tiles.py). The animation is a separate GPU particle layer that advects
 * particles along a global swell-velocity texture (data/waves_data.png) and draws each
 * as an oriented bar; it sits ABOVE the heat tiles. Both are driven by one liveLayerSync.
 */

export const VMAX_WAVES = 8.0;   // must match backend tasks/waves.py VMAX_WAVES

// Bar colour by wave height — light, translucent ticks that read over the heat field.
const BAR_PALETTE = [
    [0.80, 0.92, 1.00],   // low  - pale cyan
    [0.55, 0.80, 1.00],   // mid  - blue
    [0.95, 0.97, 1.00],   // high - near white
];
function buildBarLUT() {
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const fp = (i / 255) * (BAR_PALETTE.length - 1);
        const lo = Math.floor(fp), hi = Math.min(lo + 1, BAR_PALETTE.length - 1), f = fp - lo;
        const o = i * 4;
        for (let j = 0; j < 3; j++)
            lut[o + j] = Math.round((BAR_PALETTE[lo][j] * (1 - f) + BAR_PALETTE[hi][j] * f) * 255);
        lut[o + 3] = 255;
    }
    return lut;
}

export function loadLayer(map, config) {
    const sourceId = 'waves-source';
    const layerId  = 'waves-layer';
    const animLayerId = 'waves-anim-layer';   // created by the particle controller
    const slotId   = 'waves-legend-slot';

    let currentVersion = null;

    const worldKeyRel = (cfg) => {
        const o = cfg.outfile, i = o.lastIndexOf('.');
        const base = i !== -1 ? o.slice(0, i) : o;
        const ext  = i !== -1 ? o.slice(i)    : '';
        return `${base}_key${ext}`;
    };
    const setLegend = (cfg) => {
        const stack = document.getElementById('legend-stack');
        if (!stack) return;
        document.getElementById(slotId)?.remove();
        const slot = document.createElement('div');
        slot.id = slotId; slot.className = 'legend-slot';
        const img = document.createElement('img');
        img.src = `${window.MAP_UI}/${worldKeyRel(cfg)}?t=${Date.now()}`;
        img.style.display = 'block'; img.style.width = '100%';
        slot.appendChild(img); stack.appendChild(slot);
    };

    const tilesUrl = (version) =>
        `${window.WM_API}/tiles/waves/{z}/{x}/{y}.png?v=${version}`;

    const applyVersion = (cfg, version, maxzoom) => {
        const src = map.getSource(sourceId);
        if (src && typeof src.setTiles === 'function') {
            src.setTiles([tilesUrl(version)]);
        } else {
            if (map.getLayer(layerId))   map.removeLayer(layerId);
            if (map.getSource(sourceId)) map.removeSource(sourceId);
            map.addSource(sourceId, {
                type: 'raster', tiles: [tilesUrl(version)],
                tileSize: 256, minzoom: 0, maxzoom: maxzoom ?? 9,
            });
            // Keep the heat tiles UNDER the animated bars if the bars layer exists.
            const beforeId = map.getLayer(animLayerId) ? animLayerId : undefined;
            map.addLayer({
                id: layerId, type: 'raster', source: sourceId,
                paint: { 'raster-opacity': 0.85, 'raster-fade-duration': 0 },
            }, beforeId);
        }
        currentVersion = version;
        setLegend(cfg);
    };

    const syncTiles = (cfg) => {
        fetch(`${window.WM_API}/tiles/waves/meta?t=${Date.now()}`)
            .then((r) => (r.ok ? r.json() : null))
            .then((j) => {
                const d = j && j.data;
                if (!d || !d.available) { setLegend(cfg); return; }
                if (d.version !== currentVersion) applyVersion(cfg, d.version, d.maxzoom);
                else setLegend(cfg);
            })
            .catch(() => { /* keep current tiles on failure */ });
    };

    // Animated swell bars (GPU). Driven from this module's liveLayerSync, not its own.
    const bars = createParticleController(map, {
        sectionKey: 'waves',
        initialConfig: config,
        drawMode: 'bars',
        staticFallback: false,              // no barbs PNG; heat tiles are the base
        viewport: true,                     // render the current view (sharp on zoom-in)
        vmax: VMAX_WAVES,                   // must match backend
        colormap: () => buildBarLUT(),
        maxSpeedColor: () => VMAX_WAVES,    // colour ramp spans 0..VMAX_WAVES metres
        // dataUrl defaults to <outfile_base>_data.png = data/waves_data.png
    });

    const mount = (cfg) => {
        currentVersion = null;
        syncTiles(cfg);
        bars.mount(cfg);
    };
    const refresh = (cfg) => {
        syncTiles(cfg);
        bars.refresh(cfg);
    };
    const unmount = () => {
        currentVersion = null;
        bars.unmount();
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
        document.getElementById(slotId)?.remove();
    };

    liveLayerSync(map, {
        sectionKey: 'waves', initialConfig: config,
        mount, refresh, unmount,
    });
}