import { createFillLayer } from './_webglfill.js';
import { showLegend, removeLegend } from './_legend.js';
import { buildThresholdLUT } from './_thresholdpalette.js';

// GPU scrubber layer. Critical-zone ramp over [150, 450] Dobson Units (total column
// ozone) -- mirrors tasks/scalar_field.py's SPECS["ozone"] (architecture review
// candidate #5: restores the critical-palette behaviour PR #49 silently dropped when
// OzoneUpdater was collapsed into the generic ScalarFieldUpdater). Brightest colour
// (yellow) marks the worst reading (lowest ozone, i.e. the hole), fading through
// magenta at the critical_du threshold to a dim, near-transparent "safe" zone above it.
const VMIN = 150.0;
const VMAX = 500.0;

const PALETTES = {
    alert: [[1, 0, 1], [1, 1, 0]],           // magenta (threshold) -> yellow (worst)
    high_contrast: [[1, 0, 0], [1, 1, 0.8]], // red (threshold) -> pale yellow (worst)
};
const FLAT_COLOR = [0, 0.1, 0.3, 0.2]; // dim, mostly-transparent -- the "safe" zone

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'ozone-legend-slot';

    const addLegend = () => {
        showLegend(slotId, `${window.MAP_UI}/data/ozone_key.png?t=${Date.now()}`);
    };

    createFillLayer(map, {
        sectionKey: 'ozone',
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
            u_alpha: Number(cfg.alpha) > 0 ? Number(cfg.alpha) / 100 : 0.85,
        }),
        colormap: (cfg) => buildThresholdLUT({
            vmin: VMIN, vmax: VMAX,
            threshold: Number(cfg.critical_du) || 220.0,
            focus: 'below',
            paletteColors: PALETTES[cfg.palette] || PALETTES.alert,
            flatColor: FLAT_COLOR,
        }),
        onMount: addLegend,
        onRefresh: addLegend,
        onUnmount: () => removeLegend(slotId),
    });
}
