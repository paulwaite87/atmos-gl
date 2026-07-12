import { createFillLayer } from './_webglfill.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

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

// Discrete precip bands (mm/hr) the static contourf uses. A value falls into the
// band [LEVELS[i], LEVELS[i+1]); that band index maps to a position along the
// 7-colour ramp. There are LEVELS.length-1 = 11 bands.
const LEVELS = [0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0];
const NBANDS = LEVELS.length - 1;          // 11

// Per-band RGB, computed once from the palette exactly as the old step-LUT did
// (band index -> position along the 7-colour ramp -> interpolated colour). The
// shader holds these as a constant array and anti-aliases the boundaries between
// adjacent bands using fwidth(prate). Flat band interiors are unchanged; only the
// ~1px boundary is blended, so the banded look is preserved but the hard jagged
// edges are gone -- and it looks clean at LOW/MEDIUM LOD (no supersampling needed).
function bandColours(paletteName) {
    const palette = PALETTES[paletteName] || PALETTES.standard;
    const cols = [];
    for (let b = 0; b < NBANDS; b++) {
        const pos = b / (NBANDS - 1);              // 0..1 across the ramp
        const fp = pos * (palette.length - 1);     // 0..6
        const lo = Math.floor(fp);
        const hi = Math.min(lo + 1, palette.length - 1);
        const f = fp - lo;
        cols.push([
            palette[lo][0] * (1 - f) + palette[hi][0] * f,
            palette[lo][1] * (1 - f) + palette[hi][1] * f,
            palette[lo][2] * (1 - f) + palette[hi][2] * f,
        ]);
    }
    return cols;
}

// Emit GLSL constant arrays for the band edges and per-band colours.
function glslBandConstants(paletteName) {
    const cols = bandColours(paletteName);
    const edges = LEVELS.map((v) => v.toFixed(4)).join(', ');
    const colsGlsl = cols
        .map((c) => `vec3(${c[0].toFixed(4)}, ${c[1].toFixed(4)}, ${c[2].toFixed(4)})`)
        .join(',\n        ');
    return `
        const int NBANDS = ${NBANDS};
        const float EDGES[${LEVELS.length}] = float[${LEVELS.length}](${edges});
        const vec3 BAND_COL[${NBANDS}] = vec3[${NBANDS}](
        ${colsGlsl}
        );`;
}

// The shade() body: decode prate, discard below threshold, find the band, and
// anti-alias across the nearest band boundary using the screen-space derivative
// of prate (fwidth) -- the same edge-AA technique the isobar lines use.
function fragmentBodyFor(paletteName) {
    return `
        ${glslBandConstants(paletteName)}
        uniform float u_min;               // mm/hr threshold (below -> transparent)
        uniform float u_alpha;             // layer opacity

        int bandOf(float prate) {
            int b = 0;
            for (int k = 0; k < NBANDS; k++) {
                if (prate >= EDGES[k]) b = k;
            }
            return b;
        }

        vec4 shade(float value, vec2 uv) {
            float prate = value * value * ${VMAX.toFixed(1)};   // decode sqrt
            // A threshold of exactly 0 means "any precipitation, however light" --
            // not "include the dry areas too". prate<=0 (no rain) is always excluded,
            // independent of u_min; u_min==0 no longer paints the whole globe.
            if (prate <= 0.0 || prate < u_min) discard;

            int b = bandOf(prate);
            vec3 cHere = BAND_COL[b];

            // Distance (in mm/hr) to the nearer band boundary, and the colour on
            // the far side of that boundary.
            float loEdge = EDGES[b];
            float hiEdge = EDGES[min(b + 1, NBANDS)];
            float dLo = prate - loEdge;
            float dHi = hiEdge - prate;

            // fwidth(prate) ~ how much prate changes across one pixel. Blend over
            // ~1px around the boundary: weight 0.5 exactly on the edge.
            float aa = max(fwidth(prate), 1e-6);

            vec3 cOther;
            float w;
            if (dLo <= dHi) {
                int nb = max(b - 1, 0);
                cOther = BAND_COL[nb];
                w = clamp(0.5 + dLo / aa, 0.0, 1.0);   // 0.5 at edge -> 1 inside
            } else {
                int nb = min(b + 1, NBANDS - 1);
                cOther = BAND_COL[nb];
                w = clamp(0.5 + dHi / aa, 0.0, 1.0);
            }

            vec3 c = mix(cOther, cHere, w);
            return vec4(c, u_alpha);
        }`;
}

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'precipitation-legend-slot';

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(cfg.outfile)}?t=${Date.now()}`);
    };

    const palette = (config.palette || 'standard');

    createFillLayer(map, {
        sectionKey: 'precipitation',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: 0.0,
        vspan: 1.0,                            // value = stored sqrt-position in [0,1]
        opacity: 1.0,                          // per-pixel alpha comes from u_alpha
        bicubic: true,                         // smooth band contours at high zoom (16-tap)
        // Bands + edge-AA are computed in-shader from LEVELS, so it looks smooth at
        // LOW/MEDIUM LOD -- no heavy supersampling. resolution from cfg.level_of_detail.
        fragmentBody: fragmentBodyFor(palette),
        customUniforms: (cfg) => ({
            u_min: Number(cfg.min_mm_hr) >= 0 ? Number(cfg.min_mm_hr) : 0.1,
            u_alpha: Number.isFinite(Number(cfg.opacity)) && Number(cfg.opacity) >= 0 ? Number(cfg.opacity) / 100 : 0.9,
        }),
        onMount: addLegend,
        onRefresh: addLegend,                  // re-stamp the key image
        onUnmount: () => removeLegend(slotId),
    });
}