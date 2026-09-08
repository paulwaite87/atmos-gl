import { createFillLayer } from './_webglfill.js';
import { standardLegend } from './_legend.js';
import { buildThresholdLUT } from './_thresholdpalette.js';
import { opacityUniform } from './_opacity.js';

// GPU scrubber layer. Critical-zone ramp over precipitable water (total column
// moisture). Highlights potential problem areas (elevated moisture -- a precondition
// for heavy rain/atmospheric rivers) rather than colouring the whole globe: below
// critical_pwat is fully transparent, above it grades toward the brightest colour at
// vmax (the most anomalous reading). vmin/vmax/ticks/title come from
// fullConfig.scalar_field_specs.pwat (tasks/scalar_field.py's SPECS["pwat"], served
// by /api/config) -- the one source of truth for this field's display domain.

const PALETTES = {
    // Matches precipitation.js's "standard" palette exactly, so the two layers
    // visually reinforce each other when both render at once.
    standard: [
        [0, 1, 1], [0, 0.5, 1], [0, 1, 0], [1, 1, 0], [1, 0.5, 0], [1, 0, 0], [1, 0, 1],
    ],
    atmospheric_river: [[0, 0, 0.55], [0.6, 0, 0.85]], // deep blue -> violet (NOAA moisture-plume convention)
    deep_teal: [[0.7, 1, 1], [0, 0.35, 0.3]],           // pale cyan -> deep teal
};
const FLAT_COLOR = [0, 0, 0, 0]; // fully transparent -- unremarkable moisture

const lutFor = (cfg, vmin, vmax) => buildThresholdLUT({
    vmin, vmax,
    threshold: Number(cfg.critical_pwat) || 50.0,
    focus: 'above',
    paletteColors: PALETTES[cfg.palette] || PALETTES.standard,
    flatColor: FLAT_COLOR,
});

export function loadLayer(map, config, fullConfig = {}) {
    const { vmin: VMIN, vmax: VMAX, ticks: TICKS, title: TITLE } =
        fullConfig.scalar_field_specs.pwat;

    const legend = standardLegend('pwat-legend-slot', (cfg) => ({
        lut: lutFor(cfg, VMIN, VMAX), vmin: VMIN, vmax: VMAX, ticks: TICKS,
        title: TITLE, tickFormat: '%d',
    }), 0.85);

    createFillLayer(map, {
        sectionKey: 'pwat',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: VMIN,
        vspan: VMAX - VMIN,
        opacity: 1.0,
        bicubic: true,                         // smooth gradient at high zoom
        fragmentBody: `
            uniform float u_alpha;
            vec4 shade(float value, vec2 uv) {
                float t = clamp((value - ${VMIN.toFixed(1)}) / ${(VMAX - VMIN).toFixed(1)}, 0.0, 1.0);
                vec4 c = texture(u_cmap, vec2(t, 0.5));
                return vec4(c.rgb, c.a * u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_alpha: opacityUniform(cfg, 0.85),
        }),
        colormap: (cfg) => lutFor(cfg, VMIN, VMAX),
        onMount: legend.addLegend,
        onRefresh: legend.addLegend,
        onUnmount: legend.removeLegend,
    });
}
