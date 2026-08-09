import { createFillLayer } from './_webglfill.js';
import { CMAP_RDYLBU_R, rgbToRgba } from './_colormaps.js';
import { standardLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';

// GPU scrubber layer. Linear RdYlBu_r ramp over [-40, 50] °C -- mirrors
// tasks/scalar_field.py's SPECS["temperature"] (cmap RdYlBu_r, Normalize -40..50,
// same ticks/title) so the client-drawn legend key matches exactly.
const VMIN = -40.0;
const VMAX = 50.0;
const TICKS = [-40, -20, 0, 10, 20, 30, 40, 50];

export function loadLayer(map, config, fullConfig = {}) {
    const legend = standardLegend('temperature-legend-slot', () => ({
        lut: rgbToRgba(CMAP_RDYLBU_R),
        vmin: VMIN, vmax: VMAX, ticks: TICKS,
        title: 'Temperature (°C)', tickFormat: '%d',
    }), 0.85);

    createFillLayer(map, {
        sectionKey: 'temperature',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: VMIN,
        vspan: VMAX - VMIN,                    // value = real °C
        opacity: 1.0,                          // per-pixel alpha from u_alpha
        bicubic: true,                         // smooth gradient at high zoom
        fragmentBody: `
            uniform float u_alpha;
            vec4 shade(float value, vec2 uv) {
                float t = clamp((value - ${VMIN.toFixed(1)}) / ${(VMAX - VMIN).toFixed(1)}, 0.0, 1.0);
                vec3 c = texture(u_cmap, vec2(t, 0.5)).rgb;
                return vec4(c, u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_alpha: opacityUniform(cfg, 0.85),
        }),
        colormap: () => rgbToRgba(CMAP_RDYLBU_R),
        onMount: legend.addLegend,
        onRefresh: legend.addLegend,
        onUnmount: legend.removeLegend,
    });
}
