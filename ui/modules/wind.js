import { createParticleGLLayer } from './_particles_gl.js';
import { createFillLayer } from './_webglfill.js';
import { replaceSlot, removeLegend } from './_legend.js';

const VMAX_WIND = 40.0;   // m/s velocity-texture encoding range (must match backend)

// windy.com-style wind-speed ramp, calm -> storm. Drives the underlying SPEED HEATMAP
// (kept in sync with backend WIND_PALETTE in src/atmos_gl/tasks/wind.py). Particles no
// longer use this — they render in a single fixed colour from config (particle_color).
const PALETTE = [
    [0.25, 0.30, 0.60],   // calm   - deep blue
    [0.15, 0.60, 0.85],   // light  - cyan-blue
    [0.20, 0.75, 0.45],   // breeze - green
    [0.95, 0.90, 0.30],   // fresh  - yellow
    [0.95, 0.55, 0.20],   // strong - orange
    [0.90, 0.20, 0.20],   // gale   - red
    [0.75, 0.25, 0.85],   // storm  - violet
];

function buildLUT() {
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const fp = (i / 255) * (PALETTE.length - 1);
        const lo = Math.floor(fp), hi = Math.min(lo + 1, PALETTE.length - 1), f = fp - lo;
        const o = i * 4;
        for (let j = 0; j < 3; j++) {
            lut[o + j] = Math.round((PALETTE[lo][j] * (1 - f) + PALETTE[hi][j] * f) * 255);
        }
        lut[o + 3] = 255;
    }
    return lut;
}

// Resolve any CSS colour the browser understands ('White', 'white', '#fff', '#ffffff',
// 'rgb(...)') -> [r,g,b] 0-255, or null if not a valid colour. Uses two sentinels so an
// invalid string (which leaves fillStyle unchanged) is reliably detected.
function cssColorToRgb(str) {
    try {
        const ctx = document.createElement('canvas').getContext('2d');
        ctx.fillStyle = '#010203'; ctx.fillStyle = str; const a = ctx.fillStyle;
        ctx.fillStyle = '#040506'; ctx.fillStyle = str; const b = ctx.fillStyle;
        if (a === '#010203' && b === '#040506') return null;   // unchanged -> invalid
        if (a[0] === '#') {
            let h = a.slice(1);
            if (h.length === 3) h = h.split('').map((ch) => ch + ch).join('');
            const n = parseInt(h, 16);
            return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
        }
        const m = a.match(/rgba?\(([^)]+)\)/i);
        if (m) { const p = m[1].split(',').map((x) => Math.round(parseFloat(x))); return [p[0], p[1], p[2]]; }
        return null;
    } catch (e) { return null; }
}

// Parse a config colour: CSS name/hex/rgb() ('White', '#ffffff'), bare 'r,g,b', or
// [r,g,b] (0-255 or 0-1) -> [r,g,b] 0-255. Defaults to white.
function parseColor(c) {
    if (Array.isArray(c) && c.length >= 3) {
        const sc = (c[0] <= 1 && c[1] <= 1 && c[2] <= 1) ? 255 : 1;
        return c.slice(0, 3).map((v) => Math.max(0, Math.min(255, Math.round(v * sc))));
    }
    if (typeof c === 'string') {
        const s = c.trim();
        // bare "r,g,b" (not a CSS colour) -> parse directly
        if (/^[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*$/.test(s)) {
            const parts = s.split(',').map((p) => parseFloat(p));
            const sc = parts.every((p) => p <= 1) ? 255 : 1;
            return parts.map((p) => Math.max(0, Math.min(255, Math.round(p * sc))));
        }
        const rgb = cssColorToRgb(s);   // names, hex, rgb()
        if (rgb) return rgb;
    }
    return [255, 255, 255];   // default: white
}

// A flat 256-entry LUT of a single colour -> particles ignore speed and render one colour.
function buildFlatLUT(rgb) {
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        lut[i*4] = rgb[0]; lut[i*4+1] = rgb[1]; lut[i*4+2] = rgb[2]; lut[i*4+3] = 255;
    }
    return lut;
}

// Heatmap opacity 0..1 from heatmap_opacity (accepts a 0-1 fraction or a 0-100 percent),
// default 0.6. Drives BOTH the fill-mode per-pixel alpha and the static raster opacity.
function heatmapAlpha(cfg) {
    const op = Number(cfg.heatmap_opacity);
    if (!isFinite(op) || op < 0) return 0.6;
    return op > 1 ? Math.min(1, op / 100) : op;
}

export async function loadLayer(map, config, fullConfig = {}) {
    // Fetch the backend-computed heatmap scale (written by wind.py after scanning all
    // hours; round-tripped as wind_meta.json). Falls back to 100 km/h if missing.
    let heatmapMaxKph = 100;
    try {
        const res = await fetch(`${window.MAP_UI}/data/wind_meta.json?t=${Date.now()}`);
        if (res.ok) { const m = await res.json(); if (m.heatmap_max_kph > 0) heatmapMaxKph = m.heatmap_max_kph; }
    } catch (_) { /* use default */ }
    const vmaxMs = heatmapMaxKph / 3.6;
    const slotId = 'wind-legend-slot';
    const rgbCss = (c) => `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)})`;
    const gradient = () => PALETTE
        .map((c, i) => `${rgbCss(c)} ${(i / (PALETTE.length - 1) * 100).toFixed(1)}%`)
        .join(', ');

    const addLegend = (cfg) => {
        const vmaxKph = heatmapMaxKph;
        const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(vmaxKph * f));
        replaceSlot(slotId, (slot) => {
            slot.innerHTML = `
                <div style="font-size:11px;color:#fff;font-weight:600;margin-bottom:3px;">Wind speed (km/h)</div>
                <div style="height:10px;border-radius:2px;background:linear-gradient(to right, ${gradient()});"></div>
                <div style="display:flex;justify-content:space-between;font-size:10px;color:rgba(255,255,255,0.8);margin-top:2px;">
                    ${ticks.map(t => `<span>${t}</span>`).join('')}
                </div>`;
        });
    };

    // Keep the static-raster (non-stepping) heatmap opacity live too. Fill (stepping) mode
    // gets its opacity from u_alpha; static mode is a raster layer, so set raster-opacity
    // directly whenever config syncs.
    const applyHeatmapOpacity = (cfg) => {
        if (map.getLayer('wind-layer')) {
            try { map.setPaintProperty('wind-layer', 'raster-opacity', heatmapAlpha(cfg)); } catch (e) {}
        }
    };

    // vmaxMs set above from wind_meta.json (data-driven, rounded up to nearest 10 km/h)
    const dec = (2 * VMAX_WIND).toFixed(1), neg = VMAX_WIND.toFixed(1);
    // Decode the velocity texel (R=u, G=v) and return normalised speed |(u,v)| / vmax.
    const valueDecode = `length(vec2(d.r*${dec} - ${neg}, d.g*${dec} - ${neg})) / ${vmaxMs.toFixed(5)}`;

    // 1) Underlying windspeed HEATMAP (windy palette), beneath the particles. Re-uses the
    //    per-hour velocity _data.png (speed computed from u,v); the static .png covers the
    //    non-stepping view. beforeId keeps it under the particle layer whatever the mount order.
    const teardownHeatmap = createFillLayer(map, {
        sectionKey: 'wind',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: 0.0,
        vspan: 1.0,                      // valueDecode already returns normalised speed
        bicubic: true,
        opacity: heatmapAlpha(config),   // static-mode raster opacity (fill mode uses u_alpha)
        beforeId: 'wind-anim-layer',     // particle layer id -> heatmap stays underneath
        valueDecode,
        fragmentBody: `
            uniform float u_alpha;
            vec4 shade(float value, vec2 uv) {
                float t = clamp(value, 0.0, 1.0);
                vec3 c = texture(u_cmap, vec2(t, 0.5)).rgb;
                return vec4(c, u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_alpha: heatmapAlpha(cfg),   // fill-mode per-pixel opacity (live)
        }),
        colormap: () => buildLUT(),
        onMount: (cfg) => { addLegend(cfg); applyHeatmapOpacity(cfg); },
        onRefresh: (cfg) => { addLegend(cfg); applyHeatmapOpacity(cfg); },
        onUnmount: () => removeLegend(slotId),
    });

    // 2) Particles, exactly as before EXCEPT a single fixed colour (particle_color), which
    //    we feed as a flat LUT so the engine's speed-sampled colour is constant.
    // Particle colour: the existing `vector_color` config key (legacy wind-vector colour),
    // overridable by an explicit `particle_color`. Accepts colour names or hex.
    const colorCfg = config.particle_color != null ? config.particle_color
        : (config.vector_color != null ? config.vector_color : '#ffffff');
    const particleColor = parseColor(colorCfg);
    const teardownParticles = createParticleGLLayer(map, {
        sectionKey: 'wind',
        initialConfig: config,
        vmax: VMAX_WIND,
        colormap: () => buildFlatLUT(particleColor),   // fixed colour (not speed-based)
        maxSpeedColor: () => vmaxMs,
        staticFallback: false,           // the heatmap fill provides the static view now
        // Lifecycle/density/speed/coherence tunables fall through to engine defaults;
        // override via wind config (particle_count, particle_speed, flow_coherence_radius,
        // density_zoom_*, speed_zoom_*, trail_persist, point_size, particle_color, ...).
    });

    // Tear down both layers (particles first, then heatmap) on basemap style swap.
    return () => {
        try { teardownParticles && teardownParticles(); } catch (e) {}
        try { teardownHeatmap && teardownHeatmap(); } catch (e) {}
    };
}