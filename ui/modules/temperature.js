import { createFillLayer } from './_webglfill.js';
import { CMAP_RDYLBU_R, rgbToRgba } from './_colormaps.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

// GPU scrubber layer. Linear RdYlBu_r ramp over [-40, 50] °C, matching the static
// matplotlib colourbar key (TemperatureUpdater: cmap RdYlBu_r, Normalize -40..50).
const VMIN = -40.0;
const VMAX = 50.0;

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'temperature-legend-slot';

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(cfg.outfile)}?t=${Date.now()}`);
    };

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
            u_alpha: Number.isFinite(Number(cfg.opacity)) && Number(cfg.opacity) >= 0 ? Number(cfg.opacity) / 100 : 0.85,
        }),
        colormap: () => rgbToRgba(CMAP_RDYLBU_R),
        onMount: addLegend,
        onRefresh: addLegend,
        onUnmount: () => removeLegend(slotId),
    });
}
