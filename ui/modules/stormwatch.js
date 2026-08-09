import { createFillLayer } from './_webglfill.js';
import { CMAP_YLORRD, rgbToRgba } from './_colormaps.js';
import { standardLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';

// GPU scrubber layer (CAPE). Linear YlOrRd ramp over [0, 5000] J/kg -- mirrors
// tasks/scalar_field.py's SPECS["stormwatch"] (cmap YlOrRd, Normalize 0..5000, same
// ticks/title) so the client-drawn legend key matches exactly.
// Low CAPE is rendered transparent so the layer doesn't wash the whole globe yellow.
const VMIN = 0.0;
const VMAX = 5000.0;
const TICKS = [0, 1000, 2000, 3000, 4000, 5000];

export function loadLayer(map, config, fullConfig = {}) {
    const legend = standardLegend('stormwatch-legend-slot', () => ({
        lut: rgbToRgba(CMAP_YLORRD),
        vmin: VMIN, vmax: VMAX, ticks: TICKS,
        title: 'CAPE (J/kg)', tickFormat: '%d',
    }), 0.85);

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
        onMount: legend.addLegend,
        onRefresh: legend.addLegend,
        onUnmount: legend.removeLegend,
    });
}
