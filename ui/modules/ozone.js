import { createFillLayer } from './_webglfill.js';
import { CMAP_VIRIDIS, rgbToRgba } from './_colormaps.js';

// GPU scrubber layer. Linear viridis ramp over [200, 450] (total column ozone),
// matching the static matplotlib key (OzoneUpdater: cmap viridis, Normalize 200..450).
const VMIN = 200.0;
const VMAX = 450.0;

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'ozone-legend-slot';

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
        onUnmount: removeLegend,
    });
}
