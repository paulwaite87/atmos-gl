import { createFillLayer } from './_webglfill.js';
import { CMAP_VIRIDIS, rgbToRgba } from './_colormaps.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

// GPU scrubber layer. Linear viridis ramp over [200, 450] (total column ozone),
// matching the static matplotlib key (OzoneUpdater: cmap viridis, Normalize 200..450).
const VMIN = 200.0;
const VMAX = 450.0;

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'ozone-legend-slot';

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(cfg.outfile)}?t=${Date.now()}`);
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
                vec3 c = texture(u_cmap, vec2(t, 0.5)).rgb;
                return vec4(c, u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_alpha: Number(cfg.alpha) > 0 ? Number(cfg.alpha) / 100 : 0.85,
        }),
        colormap: () => rgbToRgba(CMAP_VIRIDIS),
        onMount: addLegend,
        onRefresh: addLegend,
        onUnmount: () => removeLegend(slotId),
    });
}
