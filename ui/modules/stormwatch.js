import { createFillLayer } from './_webglfill.js';
import { CMAP_YLORRD, rgbToRgba } from './_colormaps.js';
import { opacityUniform } from './_opacity.js';

// GPU scrubber layer (CAPE). Linear YlOrRd ramp over [0, 5000] J/kg, matching the
// static matplotlib key (StormwatchUpdater: cmap YlOrRd, Normalize 0..5000).
// Low CAPE is rendered transparent so the layer doesn't wash the whole globe yellow.
const VMIN = 0.0;
const VMAX = 5000.0;

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'stormwatch-legend-slot';

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
        sectionKey: 'stormwatch',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: VMIN,
        vspan: VMAX - VMIN,
        opacity: 1.0,
        bicubic: true,                         // smooth gradient at high zoom
        fragmentBody: `
            uniform float u_alpha;
            uniform float u_min;               // J/kg threshold; below -> transparent
            vec4 shade(float value, vec2 uv) {
                // A threshold of exactly 0 means "any CAPE, however small" -- not
                // "include zero-instability areas too". value<=0 (no CAPE) is always
                // excluded, independent of u_min; u_min==0 no longer paints the whole globe.
                if (value <= 0.0 || value < u_min) discard;
                float t = clamp((value - ${VMIN.toFixed(1)}) / ${(VMAX - VMIN).toFixed(1)}, 0.0, 1.0);
                vec3 c = texture(u_cmap, vec2(t, 0.5)).rgb;
                return vec4(c, u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_alpha: opacityUniform(cfg, 0.85),
            u_min: Number(cfg.min_cape) >= 0 ? Number(cfg.min_cape) : 250.0,
        }),
        colormap: () => rgbToRgba(CMAP_YLORRD),
        onMount: addLegend,
        onRefresh: addLegend,
        onUnmount: removeLegend,
        // key_fontsize changes never touch the fill's data texture, so the default
        // imageUrl regen chase can't detect that the legend needs re-fetching --
        // keyUrl gives it its own independent chase.
        keyUrl: keyUrlFor,
    });
}
