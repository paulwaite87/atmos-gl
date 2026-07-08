import { liveLayerSync } from './_refresh.js';
import { createParticleGLController } from './_particles_gl.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

/**
 * Waves layer = Web-Mercator heat tiles (significant wave height) + an animated swell
 * field drawn as short bars perpendicular to the wave direction (the windy.com look).
 *
 * The heat tiles are pre-rendered + published by the backend and fetched as {z}/{x}/{y}
 * (see routes/tiles.py). The animation is the shared oriented-quad particle engine
 * (_particles_gl.js, primitive: 'bar') advecting particles along a global swell-velocity
 * texture (data/waves_data.png). The bars are a MapLibre CUSTOM WEBGL LAYER: they're
 * drawn directly with the map's projection each frame, so they're sharp and scale
 * naturally on the globe instead of being a fixed image stretched over the sphere.
 * The bars sit ABOVE the heat tiles. Both are driven by one liveLayerSync.
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

    const setLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(cfg.outfile)}?t=${Date.now()}`);
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

    // Animated swell bars (GPU custom layer). Driven from this module's liveLayerSync.
    // Uses the shared particle engine with primitive:'bar' (crest perpendicular to flow,
    // fixed length). Forecast-stepped like wind: the engine subscribes to the shared
    // timeline and loads per-hour swell fields (waves_f{NNN}_data.png) via the default
    // hourDataUrl.
    const bars = createParticleGLController(map, {
        sectionKey: 'waves',
        primitive: 'bar',                   // perpendicular crest bars (windy.com swell look)
        // The engine's default renderMode ('trails') draws fade-accumulation POINTS and
        // ignores `primitive` entirely -- only 'streaks' mode dispatches through
        // buildDrawShaders(primitive), which is what actually draws bars. Force it, or
        // waves silently renders as near-invisible points instead of crest bars.
        renderMode: () => 'streaks',
        initialConfig: config,
        // Particle density per level_of_detail (1/2/3). Bars read denser than wind
        // streaks, so these are much lower than wind's defaults. Tune to taste.
        lodCount: { 1: 4000, 2: 9000, 3: 18000 },
        vmax: VMAX_WAVES,                   // must match backend
        colormap: () => buildBarLUT(),
        maxSpeedColor: () => VMAX_WAVES,    // colour ramp spans 0..VMAX_WAVES metres
        // Without this, a particle that drifts onto a no-data (land) cell just sits
        // there forever -- velocity samples as zero on land, so it never advects away,
        // rendering a static bar right at the coastline until its age naturally expires
        // (tests/gl-shaders/particles_land_reset.test.js verifies this against the real
        // UPDATE_FS shader: landReset=0 leaves a land-stuck particle unmoved; =1 resets
        // it to a random ocean-eligible position immediately). Matches currents, which
        // has always had this set.
        landReset: () => 1.0,
        // Bars are FIXED length (not speed-scaled like wind streaks), sized from the
        // bar_length config key (1..20 px, default 7) to match the old wave engine.
        lenSpeedScale: 0,
        streakLen: (cfg) => { const v = Number(cfg.bar_length);
                              return isFinite(v) ? Math.min(20, Math.max(1, v)) : 7; },
        thickness: (cfg) => { const v = Number(cfg.particle_size);
                              return isFinite(v) ? Math.min(5, Math.max(0.5, v)) : 1.5; },
        // hourDataUrl defaults to <outfile_base>_f{NNN}_data.png — the per-hour swell
        // field the collector now writes (GFS-Wave global 0p25, forecast-stepped).
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
        removeLegend(slotId);
    };

    return liveLayerSync(map, {
        sectionKey: 'waves', initialConfig: config,
        mount, refresh, unmount,
    });
}