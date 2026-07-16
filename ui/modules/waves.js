import { liveLayerSync } from './_refresh.js';
import { createFillLayer } from './_webglfill.js';
import { createParticleGLController } from './_particles_gl.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';

/**
 * Waves layer = a per-pixel GPU heat fill (significant wave height) + an animated
 * swell field drawn as short bars perpendicular to the wave direction (the windy.com
 * look). Migrated off the (now-retired) raster tile engine onto createFillLayer
 * (_webglfill.js), the same client-side GPU-shaded-mesh architecture every other
 * animated layer already uses (wind, currents, precipitation, isobars, ozone, ...) --
 * waves was the tile engine's only remaining caller. See
 * docs/adr/0005-retire-raster-tile-engine.md for the reasoning (bicubic vs the tile
 * engine's bilinear sampling, native temporal cross-fade between forecast hours,
 * instant live-config updates, simpler backend) and tasks/waves.py's _masked_uv for
 * how coastline masking moved from a live per-tile-pixel STRtree cut to a
 * baked-once-per-hour regrid+mask, shared with the swell texture the bars already used.
 *
 * Both the heat fill and the bars decode from the SAME per-hour swell velocity
 * texture (waves_f{NNN}_data.png) -- the fill via valueDecode (length(u,v), exactly
 * mirroring wind.js's speed fill), the bars via the shared particle engine
 * (_particles_gl.js, primitive: 'bar'). min_wave_height is a live client-side
 * uniform on both (u_minWave on the fill, minValue on the particle engine), not baked
 * into the texture -- see _particles_gl.js's WSAMPLE docstring for why.
 */

export const VMAX_WAVES = 8.0;   // must match backend tasks/waves.py VMAX_WAVES

// Heat-fill gradients, mirroring PALETTES in tasks/waves.py. Duplicated here the same
// way wind.js/precipitation.js mirror their own backend palettes client-side.
const PALETTES = {
    ocean_storm: [
        [0.0, 0.2, 0.4], [0.0, 0.6, 0.3], [0.9, 0.7, 0.0], [0.8, 0.2, 0.0], [0.9, 0.9, 0.9],
    ],
    neon_surge: [
        [0.0, 0.8, 1.0], [0.0, 0.95, 0.4], [1.0, 0.9, 0.0], [1.0, 0.3, 0.0], [0.9, 0.0, 0.5], [0.6, 0.0, 0.7],
    ],
    solar_flare: [
        [0.6, 1.0, 0.9], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [1.0, 0.65, 0.0], [1.0, 0.2, 0.1], [1.0, 0.0, 1.0],
    ],
};

function buildLUT(paletteName) {
    const palette = PALETTES[paletteName] || PALETTES.ocean_storm;
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const fp = (i / 255) * (palette.length - 1);
        const lo = Math.floor(fp), hi = Math.min(lo + 1, palette.length - 1), f = fp - lo;
        const o = i * 4;
        for (let j = 0; j < 3; j++)
            lut[o + j] = Math.round((palette[lo][j] * (1 - f) + palette[hi][j] * f) * 255);
        lut[o + 3] = 255;
    }
    return lut;
}

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

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'waves-legend-slot';

    const setLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(cfg.outfile)}?t=${Date.now()}`);
    };

    // Heat fill: decode swell magnitude from the per-hour swell texture (already
    // regridded + true-coastline-masked server-side -- tasks/waves.py's _masked_uv),
    // the same texture the bars below advect along. Mirrors wind.js's speed fill
    // exactly (length(u,v) normalised by vmax).
    const dec = (2 * VMAX_WAVES).toFixed(1), neg = VMAX_WAVES.toFixed(1);
    const valueDecode = `length(vec2(d.r*${dec} - ${neg}, d.g*${dec} - ${neg})) / ${VMAX_WAVES.toFixed(1)}`;

    const teardownHeatmap = createFillLayer(map, {
        sectionKey: 'waves',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: 0.0,
        vspan: 1.0,                      // valueDecode already returns normalised magnitude
        bicubic: true,
        opacity: opacityUniform(config, 0.7),
        beforeId: 'waves-anim-layer',     // particle layer id -> heatmap stays underneath
        valueDecode,
        fragmentBody: `
            uniform float u_alpha;
            uniform float u_minWave;            // metres; below -> transparent (live, real units)
            vec4 shade(float value, vec2 uv) {
                float waveH = value * ${VMAX_WAVES.toFixed(1)};
                if (waveH < u_minWave) discard;
                float t = clamp(value, 0.0, 1.0);
                vec3 c = texture(u_cmap, vec2(t, 0.5)).rgb;
                return vec4(c, u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_alpha: opacityUniform(cfg, 0.7),
            u_minWave: Math.max(0, Number(cfg.min_wave_height) || 0),
        }),
        colormap: (cfg) => buildLUT(cfg.palette || 'ocean_storm'),
        onMount: setLegend,
        onRefresh: setLegend,
        onUnmount: () => removeLegend(slotId),
    });

    // Animated swell bars (GPU custom layer). Uses the shared particle engine with
    // primitive:'bar' (crest perpendicular to flow, fixed length). Forecast-stepped
    // like wind: the engine subscribes to the shared timeline and loads per-hour
    // swell fields (waves_f{NNN}_data.png) via the default hourDataUrl. Unlike
    // createFillLayer, this engine doesn't self-manage config sync -- driven below by
    // this module's own liveLayerSync, same as the pre-migration version.
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
        // Live minimum-wave-height threshold: below it, a cell is treated as no-data
        // for the particles too, same as land (WSAMPLE folds this into hasData) -- a
        // deliberate behaviour change from the pre-migration tile engine, where
        // min_wave_height only ever hid the heat fill, never affected the bars.
        minValue: (cfg) => Math.max(0, Number(cfg.min_wave_height) || 0),
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

    const unsubBars = liveLayerSync(map, {
        sectionKey: 'waves', initialConfig: config,
        mount: (cfg) => bars.mount(cfg),
        refresh: (cfg) => bars.refresh(cfg),
        unmount: () => bars.unmount(),
    });

    return () => {
        try { unsubBars && unsubBars(); } catch (e) {}
        try { teardownHeatmap && teardownHeatmap(); } catch (e) {}
    };
}
