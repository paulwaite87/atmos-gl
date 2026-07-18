import { createFillLayer, cssToRgb } from './_webglfill.js';
import { opacityUniform } from './_opacity.js';

// GPU Polar Boundary line: the 0degC isotherm (the "freezing line" NZ weather
// broadcasts show creeping up from the Antarctic in winter), rendered from the SAME
// temperature field temperature.js renders as a filled heatmap -- just a single
// contour line instead of a colour ramp. Shares temperature.js's vmin/vspan (-40..50
// degC) since it decodes the identical encode_frames() convention (see
// tasks/polar_boundary.py).
const VMIN = -40.0;
const VMAX = 50.0;
const FREEZE_LEVEL_C = 0.0;

export function loadLayer(map, config, fullConfig = {}) {
    return createFillLayer(map, {
        sectionKey: 'polar_boundary',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: VMIN,
        vspan: VMAX - VMIN,
        bicubic: true,                         // smooth line path at high zoom
        // Single-level version of isobars.js's line-drawing trick: distance to the one
        // fixed level (not a repeating grid), in screen pixels via fwidth-based AA.
        fragmentBody: `
            uniform float u_level;             // freezing point, degC
            uniform float u_linewidth;         // line width in SCREEN px
            uniform vec3  u_linecolor;
            uniform float u_alpha;
            // Caps the local derivative used for the width/AA math. A genuine data
            // discontinuity (e.g. a sharp land/sea temperature edge the backend's
            // smoothing didn't fully erase) can spike fwidth() far beyond anything a
            // smooth synoptic-scale gradient produces, which otherwise fools "distance
            // to the line, in pixels" into treating a value nowhere near freezing as if
            // it sat right on the boundary -- a false wide patch instead of a thin
            // line. 5 degC/pixel comfortably exceeds any real gradient at the zoom
            // levels this layer renders at.
            const float MAX_DERIVATIVE = 5.0;
            vec4 shade(float value, vec2 uv) {
                float dist = abs(value - u_level);
                float aa = min(fwidth(value), MAX_DERIVATIVE);
                if (aa <= 0.0) discard;
                float px = dist / aa;
                float halfW = max(u_linewidth, 0.5) * 0.5;
                float lineAlpha = 1.0 - smoothstep(halfW - 0.5, halfW + 0.5, px);
                if (lineAlpha <= 0.001) discard;
                return vec4(u_linecolor, lineAlpha * u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_level: FREEZE_LEVEL_C,
            u_linewidth: Number(cfg.linewidth) > 0 ? Number(cfg.linewidth) : 2.0,
            u_linecolor: cssToRgb(cfg.line_color),
            u_alpha: opacityUniform(cfg, 0.9),
        }),
    });
}
