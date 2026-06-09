import { createAnimatedRasterLayer } from './_webglanim.js';

// Top of the precip scale (mm/hr). MUST match VMAX_PRECIP in the backend, which
// sqrt-encodes the data texture against it. The helper hands shade() the raw
// stored value (vmin:0, vspan:1) -> that value is the sqrt position in [0,1].
const VMAX = 100.0;

// Palettes mirror PrecipitationUpdater.PALETTES (backend) so the animated colour
// ramp matches the static render + colourbar key.
const PALETTES = {
    standard: [
        [0.0, 1.0, 1.0], [0.0, 0.5, 1.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0],
        [1.0, 0.5, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 1.0],
    ],
    ocean_blue: [
        [0.8, 0.9, 1.0], [0.6, 0.8, 1.0], [0.4, 0.6, 1.0], [0.2, 0.4, 1.0],
        [0.0, 0.2, 0.8], [0.0, 0.0, 0.6], [0.0, 0.0, 0.4],
    ],
    high_contrast: [
        [0.0, 0.9, 0.0], [0.0, 0.6, 0.0], [1.0, 1.0, 0.0], [1.0, 0.6, 0.0],
        [1.0, 0.0, 0.0], [0.7, 0.0, 0.0], [1.0, 0.0, 1.0],
    ],
};

// Discrete precip bands (mm/hr) the static contourf uses. A value falls in
// interval i; that interval maps to a position along the 7-colour ramp.
const LEVELS = [0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0];

// Build a 256x1 RGBA colour LUT indexed by sqrt-position t (= the value the
// shader receives). LUT[i] is the palette colour for prate = (i/255)^2 * VMAX,
// looked up through LEVELS exactly as matplotlib's BoundaryNorm would.
function buildLUT(paletteName) {
    const palette = PALETTES[paletteName] || PALETTES.standard;
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const t = i / 255;
        const prate = t * t * VMAX;
        let bin = 0;
        for (let k = 0; k < LEVELS.length; k++) if (prate >= LEVELS[k]) bin = k;
        bin = Math.min(bin, LEVELS.length - 2);            // 0..10
        const pos = bin / (LEVELS.length - 2);             // 0..1 across the ramp
        const fp = pos * (palette.length - 1);             // 0..6
        const lo = Math.floor(fp);
        const hi = Math.min(lo + 1, palette.length - 1);
        const f = fp - lo;
        const o = i * 4;
        for (let j = 0; j < 3; j++) {
            lut[o + j] = Math.round((palette[lo][j] * (1 - f) + palette[hi][j] * f) * 255);
        }
        lut[o + 3] = 255;                                  // alpha applied in shade()
    }
    return lut;
}

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'precipitation-legend-slot';

    const keyUrlFor = (cfg) => {
        const o = cfg.outfile, i = o.lastIndexOf('.');
        const base = i !== -1 ? o.slice(0, i) : o;
        const ext = i !== -1 ? o.slice(i) : '';
        return `${window.MAP_UI}/${base}_key${ext}`;
    };
    const addLegend = (cfg) => {
        const stack = document.getElementById('legend-stack');
        if (!stack) return;
        document.getElementById(slotId)?.remove();
        const slot = document.createElement('div');
        slot.id = slotId; slot.className = 'legend-slot';
        const img = document.createElement('img');
        img.src = `${keyUrlFor(cfg)}?t=${Date.now()}`;
        img.style.display = 'block'; img.style.width = '100%';
        slot.appendChild(img); stack.appendChild(slot);
    };
    const removeLegend = () => document.getElementById(slotId)?.remove();

    createAnimatedRasterLayer(map, {
        sectionKey: 'precipitation',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: 0.0,
        vspan: 1.0,                            // value = stored sqrt-position in [0,1]
        opacity: 1.0,                          // per-pixel alpha comes from u_alpha
        // resolution from cfg.level_of_detail; timing/sharpness from the global [animation] section
        fragmentBody: `
            uniform float u_min;               // mm/hr threshold (below -> transparent)
            uniform float u_alpha;             // layer opacity
            vec4 shade(float value, vec2 uv) {
                float prate = value * value * ${VMAX.toFixed(1)};   // decode sqrt
                if (prate < u_min) discard;
                vec3 c = texture(u_cmap, vec2(value, 0.5)).rgb;     // sqrt-position LUT
                return vec4(c, u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_min: Number(cfg.min_mm_hr) >= 0 ? Number(cfg.min_mm_hr) : 0.1,
            u_alpha: Number(cfg.alpha) > 0 ? Number(cfg.alpha) : 0.9,
        }),
        colormap: (cfg) => buildLUT(cfg.palette || 'standard'),
        onMount: addLegend,
        onRefresh: addLegend,                  // re-stamp the key image
        onUnmount: removeLegend,
    });
}