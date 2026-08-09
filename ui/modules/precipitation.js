import { createFillLayer } from './_webglfill.js';
import { standardLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';
import { buildSteppedLUT } from './_thresholdpalette.js';

// Top of the precip scale (mm/hr). MUST match VMAX_PRECIP in the backend, which
// sqrt-encodes the data texture against it -- used here only to convert LEVELS/
// min_mm_hr (real mm/hr) into that same sqrt position (see toSqrtPos below), not to
// decode anything in the shader.
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

// Discrete precip bands (mm/hr) the static contourf AND this layer's colour LUT both
// use. A value falls into the band [LEVELS[i], LEVELS[i+1]); mirrors
// precipitation.py's LEVELS. There are LEVELS.length-1 = 11 bands.
const LEVELS = [0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0];

const FLAT_COLOR = [0, 0, 0, 0]; // fully transparent -- below threshold / no rain

// The colour LUT's domain is built in the SAME sqrt-encoded [0,1] space the backend
// stores in the data texture (see encode_frames' transform="sqrt"), not plain mm/hr
// -- a linear mm/hr domain over only 256 LUT entries gives each entry a ~0.4mm/hr
// step, so anything below ~0.2mm/hr would round down to the same "zero" entry as
// true-dry pixels, silently discarding exactly the fine low-end precision the
// backend's sqrt encoding exists to preserve. Converting LEVELS/minValue into this
// same sqrt position concentrates the LUT's resolution at the low end to match,
// and means shade() below needs no decode step at all -- it looks the stored value
// up directly.
const toSqrtPos = (mmPerHour) => Math.sqrt(Math.max(0, mmPerHour) / VMAX);
const LEVELS_SQRT = LEVELS.map(toSqrtPos);

// Legend key ticks -- a coarser subset of LEVELS (matches the pre-migration server
// key's own deliberately-simplified tick set; the full 11-band LEVELS is more detail
// than a legend needs). toPos places them via the SAME sqrt-position domain the LUT
// itself is built in (see lutFor/LEVELS_SQRT above) -- ticks and bar always agree.
const KEY_TICKS = [0.1, 1.0, 5.0, 15.0, 50.0, 100.0];

const lutFor = (cfg) => buildSteppedLUT({
    vmin: 0.0, vmax: 1.0,
    minValue: toSqrtPos(Number(cfg.min_mm_hr) >= 0 ? Number(cfg.min_mm_hr) : 0.1),
    levels: LEVELS_SQRT,
    paletteColors: PALETTES[cfg.palette] || PALETTES.standard,
    flatColor: FLAT_COLOR,
});

export function loadLayer(map, config, fullConfig = {}) {
    const legend = standardLegend('precipitation-legend-slot', (cfg) => ({
        lut: lutFor(cfg), toPos: toSqrtPos, ticks: KEY_TICKS,
        title: 'Precipitation (mm/hr)', tickFormat: '%.1f',
    }), 0.9);

    createFillLayer(map, {
        sectionKey: 'precipitation',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: 0.0,
        vspan: 1.0,                            // value = stored sqrt-position in [0,1]
        opacity: 1.0,                          // per-pixel alpha comes from u_alpha
        // Bicubic, matching every other LUT-based layer (pwat/ozone/temperature) --
        // safe here now that colour comes from a single smooth texture(u_cmap, ...)
        // lookup (see colormap below) rather than a per-pixel discrete band decision
        // in the shader: there's no hard branch left for a bicubic overshoot to flip.
        bicubic: true,
        // shade() just looks the stored value up directly -- no decode step, since
        // the LUT's own domain is built in this same sqrt-position space (see
        // LEVELS_SQRT/colormap below). Thresholding, banding and the outer
        // transparent cutoff are all baked into the LUT, the same shape pwat/ozone
        // already use for their own critical-threshold ramps. No fwidth()/discard
        // logic at all: the GPU's own bilinear filtering of the (already backend-
        // smoothed) data texture and of this LUT is what makes band + outer-edge
        // boundaries smooth.
        fragmentBody: `
            uniform float u_alpha;
            vec4 shade(float value, vec2 uv) {
                vec4 c = texture(u_cmap, vec2(value, 0.5));
                return vec4(c.rgb, c.a * u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_alpha: opacityUniform(cfg, 0.9),
        }),
        colormap: (cfg) => lutFor(cfg),
        onMount: legend.addLegend,
        onRefresh: legend.addLegend,
        onUnmount: legend.removeLegend,
    });
}
