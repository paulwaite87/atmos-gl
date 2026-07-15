// ui/modules/_particlegl_primitives.js
/**
 * Low-level WebGL plumbing shared between wind/waves' oriented-quad particle engine
 * (_particles_gl.js) and currents/wind's streamline-ribbon engine
 * (_currentparticles_gl.js) -- architecture review candidate #4, "low-level WebGL
 * plumbing duplicated between wind's and currents' particle engines". The two
 * engines' actual physics (oriented-quad streaks/bars vs. upstream-integrated
 * streamlines) is genuinely different and stays in its own file -- this module owns
 * only the device-level GL setup that has no coupling to either engine's physics:
 * shader compile/link, texture/state-texture creation, and per-particle RNG-texture
 * builders.
 *
 * randomState() is deliberately NOT here: its floatPos branch is near-identical
 * between the two engines, but its non-floatPos (16-bit packed) fallback branch
 * encodes bytes differently in each file to match that file's own position-packing
 * scheme -- forcing it into one shared function would mean baking a per-caller
 * packing callback into it, not a clean extraction. Left duplicated per-file.
 */

export function compile(gl, type, src, label) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        console.error(`[${label}] shader compile:`, gl.getShaderInfoLog(sh));
        return null;
    }
    return sh;
}

// Every program goes through here, including ones (coherence, blend) that never
// reference PACK/decodePos -- an unused #define is harmless, so no need to
// special-case which programs actually need the POS_FLOAT injection.
export function linkProg(gl, vs, fs, floatPos, label) {
    if (floatPos) {
        const inj = (src) => src.replace('#version 300 es\n', '#version 300 es\n#define POS_FLOAT 1\n');
        vs = inj(vs); fs = inj(fs);
    }
    const v = compile(gl, gl.VERTEX_SHADER, vs, label), f = compile(gl, gl.FRAGMENT_SHADER, fs, label);
    if (!v || !f) return null;
    const p = gl.createProgram();
    gl.attachShader(p, v); gl.attachShader(p, f); gl.linkProgram(p);
    gl.deleteShader(v); gl.deleteShader(f);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
        console.error(`[${label}] link:`, gl.getProgramInfoLog(p));
        return null;
    }
    return p;
}

export function makeTex(gl, w, h, data, filter, wrapS) {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrapS || gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
    return t;
}

// Position state texture: RGBA32F (full float precision) when supported, else the
// 16-bit packed RGBA8 path via makeTex. NEAREST sampled either way (exact per-particle
// fetch), so no float-linear extension is needed.
export function makeStateTex(gl, w, h, data, floatPos) {
    if (!floatPos) return makeTex(gl, w, h, data, gl.NEAREST);
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, w, h, 0, gl.RGBA, gl.FLOAT, data);
    return t;
}

// Random initial ages (not all-zero) so particles don't all pop into existence
// synchronised on first load. g = random per-particle lifetime factor so particles
// don't all die in lockstep either.
export function randomAge(RES) {
    const data = new Uint8Array(RES * RES * 4);
    for (let i = 0; i < RES * RES; i++) {
        data[i*4+0] = Math.floor(Math.random() * 256);   // age
        data[i*4+1] = Math.floor(Math.random() * 256);   // lifetime factor
        data[i*4+2] = 0;
        data[i*4+3] = 255;
    }
    return data;
}

export const QUAD_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() { v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;

// Encoded R/G are an AFFINE map of velocity (v = ch*2*vmax - vmax), so a linear mix of
// the encoded channels equals a linear mix of the decoded velocity -- the raw textures
// can be lerped directly here.
export const BLEND_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_a;      // current hour
uniform sampler2D u_b;      // next hour
uniform float u_blend;      // frac toward next hour, 0..1
void main(){
    fragColor = mix(texture(u_a, v_uv), texture(u_b, v_uv), u_blend);
}`;

// DIRECTION-COHERENCE filter (the old backend smooth_flow_direction, moved to the GPU so
// flow_coherence_radius is live-tunable). Coarse 0.25-deg GFS renders a shear as an abrupt
// 1-cell direction flip; interpolating the raw Cartesian U/V across it makes the opposing
// components cancel, collapsing magnitude into a low-speed "dead zone" right at the seam, so
// particles dwell (bright lines) or stall (hard seam between independently-moving regions).
// We rewrite each cell as SPEED * smoothed-unit-DIRECTION: each cell keeps its OWN magnitude
// (so the wind-speed colours / fine detail are untouched) but its flow DIRECTION is averaged
// as a unit vector over a ~radius-cell Gaussian, turning the flip into a gradual coherent
// turn. Done SEPARABLY (H then V) so a large radius (sigma ~8) stays cheap, and only when a
// texture loads or the radius changes — never per frame. The unit components are averaged
// linearly across both passes and renormalised ONCE at the end (correct separable Gaussian).
//
// Pass H -> cohTempTex:  RG = horizontally-averaged unit-dir (signed, *0.5+0.5), B = mag/vmax,
//                        A = coverage. Longitude wraps; far taps (>2.5 sigma) are skipped so
//                        texture-read cost tracks the radius despite the fixed loop bound.
export const COH_H_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_src;
uniform float u_vmax, u_radius;
uniform vec2 u_texel;
const int MAXK = 24;
void main(){
    vec4 c = texture(u_src, v_uv);
    vec2 cv = c.rg * (2.0*u_vmax) - u_vmax;
    float mag = length(cv);
    float sig = max(u_radius, 1e-3);
    float lim = 2.5 * sig;
    vec2 acc = vec2(0.0); float wsum = 0.0;
    for (int k = -MAXK; k <= MAXK; k++){
        float fk = float(k);
        if (abs(fk) > lim) continue;
        float w = exp(-(fk*fk)/(2.0*sig*sig));
        vec4 s = texture(u_src, vec2(fract(v_uv.x + fk*u_texel.x + 1.0), v_uv.y));
        vec2 sv = s.rg * (2.0*u_vmax) - u_vmax;
        float m = length(sv);
        if (m > 0.01 && s.a > 0.5){ acc += w * (sv / m); wsum += w; }
    }
    vec2 hd = (wsum > 0.0) ? acc / wsum : (mag > 0.01 ? cv / mag : vec2(0.0));
    fragColor = vec4(hd * 0.5 + 0.5, mag / u_vmax, c.a);
}`;

// Pass V (reads cohTempTex) -> coherent encoded texture: average the horiz-smoothed unit-dir
// VERTICALLY (latitude clamps at the poles), renormalise once, multiply back by THIS cell's
// preserved magnitude, and re-encode in the same rg*(2*vmax)-vmax form the sampler decodes.
export const COH_V_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_src;
uniform float u_vmax, u_radius;
uniform vec2 u_texel;
const int MAXK = 24;
void main(){
    vec4 c = texture(u_src, v_uv);
    float mag = c.b * u_vmax;          // this cell's own (preserved) speed
    float sig = max(u_radius, 1e-3);
    float lim = 2.5 * sig;
    vec2 acc = vec2(0.0); float wsum = 0.0;
    for (int k = -MAXK; k <= MAXK; k++){
        float fk = float(k);
        if (abs(fk) > lim) continue;
        float w = exp(-(fk*fk)/(2.0*sig*sig));
        vec4 s = texture(u_src, vec2(v_uv.x, clamp(v_uv.y + fk*u_texel.y, 0.0, 1.0)));
        if (s.a > 0.5){ acc += w * (s.rg * 2.0 - 1.0); wsum += w; }
    }
    vec2 d = (wsum > 0.0) ? acc / wsum : (c.rg * 2.0 - 1.0);
    float dl = length(d);
    vec2 outv = (dl > 1e-4) ? mag * (d / dl) : vec2(0.0);
    fragColor = vec4(clamp((outv + u_vmax) / (2.0*u_vmax), 0.0, 1.0), 0.0, c.a);
}`;
