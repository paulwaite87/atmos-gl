// ui/modules/_opacity.js
/**
 * Shared opacity-from-config normaliser behind precipitation.js, stormwatch.js,
 * ozone.js, pwat.js, temperature.js, currents.js, and wind.js -- architecture review
 * candidate "extract opacityUniform from seven copies". Each of them independently
 * re-derived "cfg.opacity (0-100) -> 0-1 float, falling back to a layer-specific
 * default when unset/invalid" -- the duplication let a fix to one copy (opacity=0
 * being treated as falsy and falling back to the default instead of rendering fully
 * transparent) land in six places and still miss a seventh call site in the same
 * file (currents.js's static-mode `opacity` param, separate from its `u_alpha`).
 *
 * Named and exported (rather than left as an inline expression) so it can be unit
 * tested directly, mirroring _particles_gl.js's defaultAlpha/defaultSpeed.
 */

// cfg.opacity: 0-100, clamped both ends, falling back to `fallback` (already a 0-1
// float) when unset or not a finite number. An explicit 0 is honoured -- it must
// never fall back to `fallback`, or a layer intentionally set fully transparent
// renders at its default opacity instead.
export function opacityUniform(cfg, fallback) {
    const v = Number(cfg.opacity);
    if (!Number.isFinite(v)) return fallback;
    return Math.min(100, Math.max(0, v)) / 100;
}
