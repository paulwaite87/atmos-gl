import { createFillLayer, cssToRgb } from './_webglfill.js';
import { opacityUniform } from './_opacity.js';

// GPU Polar Boundary line: an isotherm (the "freezing line" NZ weather broadcasts show
// creeping up from the Antarctic in winter -- 0 degC by default, but the level itself
// is the freeze_level_c setting, a -5..+5 slider), rendered from the SAME temperature
// field temperature.js renders as a filled heatmap -- just a single contour line
// instead of a colour ramp. Shares temperature.js's vmin/vspan (-40..50 degC) since it
// decodes the identical encode_frames() convention (see tasks/polar_boundary.py).
const VMIN = -40.0;
const VMAX = 50.0;
// Always 0 regardless of freeze_level_c -- NOT a temperature. The backend encodes a
// signed DISTANCE (in degrees latitude) from whichever isotherm freeze_level_c asked
// it to find, not the temperature itself, so "on the boundary" is always exactly 0 in
// the texture's own value space; see _isolate_antarctic_boundary's docstring.
const BOUNDARY_LEVEL = 0.0;

const LEVEL_MIN = -5, LEVEL_MAX = 5;

// Mirrors tasks/polar_boundary.py's _level_tag() exactly -- must stay in sync, since
// this is what turns cfg.freeze_level_c into the filename suffix
// PolarBoundaryUpdater.plot()/publish_current_hour() actually wrote to disk (one GPU
// texture pre-rendered per integer level, LEVEL_MIN..LEVEL_MAX, every forecast hour --
// see that class's docstring for why: switching levels is then just fetching a
// different pre-baked file, no backend re-render to wait on).
export function levelTag(n) {
    if (n < 0) return `m${-n}`;
    if (n > 0) return `p${n}`;
    return '0';
}

// Clamped, rounded-to-integer freeze_level_c -- matches plot()'s own clamp against a
// stale/hand-edited config value outside the slider's range.
export function clampedLevel(cfg) {
    const raw = Number(cfg.freeze_level_c);
    const n = Number.isFinite(raw) ? Math.round(raw) : 0;
    return Math.max(LEVEL_MIN, Math.min(LEVEL_MAX, n));
}

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
            u_level: BOUNDARY_LEVEL,
            u_linewidth: Number(cfg.linewidth) > 0 ? Number(cfg.linewidth) : 2.0,
            u_linecolor: cssToRgb(cfg.line_color),
            u_alpha: opacityUniform(cfg, 0.9),
        }),
        // Per-hour texture for whichever level freeze_level_c currently names --
        // PolarBoundaryUpdater.plot() pre-renders one per integer level every hour, so
        // this is just a different filename, not a different render pipeline.
        hourDataUrl: (cfg, hour, bust) => {
            const base = cfg.outfile.replace(/\.png$/, '');
            const f = String(hour).padStart(3, '0');
            const tag = levelTag(clampedLevel(cfg));
            return `${window.MAP_UI}/${base}_f${f}_lvl${tag}_data.png?t=${bust}`;
        },
        // A live config change to freeze_level_c must invalidate the per-hour texture
        // cache (keyed by hour only) -- otherwise an hour already cached under the OLD
        // level's URL would keep showing that level until its cache entry happened to
        // evict for an unrelated reason. See _webglfill.js's cacheKey docstring.
        cacheKey: (cfg) => levelTag(clampedLevel(cfg)),
        // Static (non-WebGL / forecast_stepping off) fallback intentionally NOT
        // overridden -- it stays the plain, un-leveled filename (default staticUrl),
        // which plot() always renders at the persisted freeze_level_c: there's no live
        // slider concept for a non-interactive fallback image.
    });
}
