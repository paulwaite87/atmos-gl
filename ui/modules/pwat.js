import { createFillLayer } from './_webglfill.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';
import { buildThresholdLUT } from './_thresholdpalette.js';

// GPU scrubber layer. Critical-zone ramp over [0, 80] mm precipitable water (total
// column moisture) -- mirrors tasks/scalar_field.py's SPECS["pwat"]. Highlights
// potential problem areas (elevated moisture -- a precondition for heavy rain/
// atmospheric rivers) rather than colouring the whole globe: below critical_pwat is
// fully transparent, above it grades toward the brightest colour at vmax (the most
// anomalous reading).
const VMIN = 0.0;
const VMAX = 80.0;

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

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'pwat-legend-slot';

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(cfg.outfile)}?t=${Date.now()}`);
    };

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
            u_alpha: Number.isFinite(Number(cfg.opacity)) && Number(cfg.opacity) >= 0 ? Number(cfg.opacity) / 100 : 0.85,
        }),
        colormap: (cfg) => buildThresholdLUT({
            vmin: VMIN, vmax: VMAX,
            threshold: Number(cfg.critical_pwat) || 50.0,
            focus: 'above',
            paletteColors: PALETTES[cfg.palette] || PALETTES.standard,
            flatColor: FLAT_COLOR,
        }),
        onMount: addLegend,
        onRefresh: addLegend,
        onUnmount: () => removeLegend(slotId),
    });
}
