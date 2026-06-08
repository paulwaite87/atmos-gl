import { createAnimatedRasterLayer, cssToRgb } from './_webglanim.js';

export function loadLayer(map, config) {
    createAnimatedRasterLayer(map, {
        sectionKey: 'isobars',
        initialConfig: config,
        vmin: 950.0,
        vspan: 100.0,                          // 1050 - 950, matches backend encode
        opacity: 0.85,
        // resolution comes from cfg.level_of_detail; sharpness from cfg.animation_sharp
        fragmentBody: `
            uniform float u_interval;          // hPa between isobars
            uniform float u_linewidth;         // line width in canvas px
            uniform vec3  u_linecolor;
            vec4 shade(float value, vec2 uv) {
                float f = value / u_interval;
                float distToLine = abs(fract(f + 0.5) - 0.5);   // f-units to nearest contour
                float aa = fwidth(f);                            // f-units per canvas pixel
                if (aa <= 0.0) discard;
                float px = distToLine / aa;                      // pixels to nearest contour
                float halfW = max(u_linewidth, 0.5) * 0.5;
                float alpha = 1.0 - smoothstep(halfW - 0.5, halfW + 0.5, px);
                if (alpha <= 0.001) discard;
                return vec4(u_linecolor, alpha);
            }`,
        customUniforms: (cfg) => ({
            u_interval: Number(cfg.isobar_step) > 0 ? Number(cfg.isobar_step) : 4.0,
            u_linewidth: Number(cfg.linewidth) > 0 ? Number(cfg.linewidth) : 1.4,
            u_linecolor: cssToRgb(cfg.isobar_color),
        }),
    });
}
