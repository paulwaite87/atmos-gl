import { liveLayerSync } from './_refresh.js';
import { timeline } from './timeline.js';
import { scrubber } from './scrubber.js';

/**
 * Wind particles as a MapLibre v5 CUSTOM WEBGL LAYER (sharp, globe-correct).
 *
 * This is the wind counterpart to _waveparticles.js. Instead of advecting particles
 * into an offscreen canvas that MapLibre stretches over the globe (fuzzy on zoom), it
 * draws each particle DIRECTLY into the map's GL context every frame using MapLibre's
 * `projectTile` projection — so particles are rasterised at screen resolution and follow
 * the globe exactly, staying crisp and constant-sized as you zoom.
 *
 * Wind's signature is a fading trail, accumulated over many frames in a screen buffer.
 * That doesn't survive a rotating globe (an accumulated screen buffer smears along the
 * camera motion), so here each particle is drawn as a short STREAK ALONG the flow every
 * frame — an oriented quad whose length scales with speed and whose opacity fades from a
 * bright leading edge to a faint tail, implying motion. Sharp, zoom-scaling, no smear.
 * (A true comet tail is possible later via in-shader backward integration.)
 *
 * Animated path = the custom WebGL layer. Static path = the barbs PNG raster layer
 * (unchanged fallback when animation is off or WebGL is unavailable). Drop-in for wind.js
 * via createWindParticleGLLayer (mount/refresh/unmount driven by liveLayerSync).
 * The wave module (_waveparticles.js) is left untouched.
 */

const MERCATOR_CORNERS = [
    [-180, 85.051129], [180, 85.051129], [180, -85.051129], [-180, -85.051129],
];
const lodOf = (cfg) => { const n = parseInt(cfg.level_of_detail, 10); return (n === 1 || n === 3) ? n : 2; };
const LOD_COUNT = { 1: 6000, 2: 12000, 3: 24000 };

// ---- shaders --------------------------------------------------------------

const QUAD_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() { v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;

const PACK = `
vec2 packPos(float x){ float e = floor(clamp(x,0.0,1.0)*65535.0 + 0.5);
    float hi = floor(e/256.0); return vec2(hi, e - hi*256.0)/255.0; }
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
vec4 encodePos(vec2 p){ return vec4(packPos(p.x), packPos(p.y)); }
float rand(vec2 co){ return fract(sin(dot(co, vec2(12.9898,78.233))) * 43758.5453); }`;

// Advection in equirectangular [0,1] space. Wind propagates ALONG the encoded velocity
// (no sign flip — that flip is a waves-only convention). Respawns inside the view box,
// which may wrap the antimeridian (bmin.x > bmax.x).
const UPDATE_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_particles;
uniform sampler2D u_wind;
uniform float u_vmax, u_speed, u_dropRate, u_dropBump, u_dropSpeed, u_seed, u_landReset;
uniform vec4 u_bboxPos;
const float PI = 3.141592653589793;
const float STEP = 0.0005;
${PACK}
void main(){
    vec2 pos = decodePos(texture(u_particles, v_uv));
    float lat = (0.5 - pos.y) * PI;
    vec4 w = texture(u_wind, pos);
    vec2 vel = (w.a < 0.5) ? vec2(0.0) : (w.rg * (2.0*u_vmax) - u_vmax);
    float coslat = max(cos(lat), 0.05);
    vec2 d = vec2(vel.x / coslat * 0.5, -vel.y) * (u_speed * STEP);
    vec2 npos = pos + d;
    npos.x = fract(npos.x + 1.0);
    float speed = length(vel);
    float drop = u_dropRate + (1.0 - clamp(speed/u_dropSpeed, 0.0, 1.0)) * u_dropBump;
    vec2 seed = (pos + v_uv) * (u_seed + 1.0);
    vec2 bmin = u_bboxPos.xy, bmax = u_bboxPos.zw;
    bool lonWrap = bmin.x > bmax.x;
    float rlon;
    if (!lonWrap) {
        rlon = bmin.x + rand(seed + 1.3) * (bmax.x - bmin.x);
    } else {
        float wlo = 1.0 - bmin.x, whi = bmax.x;
        float r = rand(seed + 1.3) * (wlo + whi);
        rlon = (r < wlo) ? (bmin.x + r) : (r - wlo);
    }
    float rlat = bmin.y + rand(seed + 2.7) * (bmax.y - bmin.y);
    vec2 randPos = vec2(rlon, rlat);
    bool lonOut = lonWrap ? (npos.x < bmin.x && npos.x > bmax.x)
                          : (npos.x < bmin.x || npos.x > bmax.x);
    bool outside = lonOut || (npos.y < bmin.y) || (npos.y > bmax.y);
    bool reset = (rand(seed) < drop) || (npos.y <= 0.0) || (npos.y >= 1.0)
                 || (u_landReset > 0.5 && w.a < 0.5)
                 || outside;
    fragColor = encodePos(reset ? randPos : npos);
}`;

// Streak vertex shader BODY (MapLibre projection prelude + define are prepended). We
// project the streak centre (and a neighbour ahead, for orientation) and build a thin
// quad in screen space, its LONG axis along the flow. Length scales with speed; v_along
// carries -1 (tail) .. +1 (head) for the opacity gradient. WV_-prefixed constants avoid
// clashing with names the prelude defines (e.g. PI).
const STREAK_VS_BODY = `
precision highp float;
uniform sampler2D u_particles;
uniform sampler2D u_wind;
uniform float u_res, u_vmax, u_halfLen, u_halfThick, u_eps, u_maxspeed, u_lenSpeedScale;
uniform vec2 u_viewport;
out float v_speed;
out float v_along;
const float WV_PI = 3.141592653589793;
const float WV_LATMAX = 1.4844222297453324;
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
vec2 toMerc(vec2 p){
    float lat = clamp((0.5 - p.y) * WV_PI, -WV_LATMAX, WV_LATMAX);
    float my = log(tan(WV_PI*0.25 + lat*0.5));
    return vec2(p.x, 0.5 - my/(2.0*WV_PI));
}
void main(){
    int pid = gl_VertexID / 6;
    int corner = gl_VertexID - pid*6;
    float col = mod(float(pid), u_res);
    float row = floor(float(pid) / u_res);
    vec4 s = texelFetch(u_particles, ivec2(int(col), int(row)), 0);
    vec2 pos = decodePos(s);
    vec4 w = texture(u_wind, pos);
    if (w.a < 0.5) { v_speed = 0.0; v_along = 0.0; gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
    vec2 vel = w.rg * (2.0*u_vmax) - u_vmax;
    float spd = length(vel);
    v_speed = spd;

    float lat = (0.5 - pos.y) * WV_PI;
    float coslat = max(cos(lat), 0.05);
    vec2 dirEq = vec2(vel.x / coslat, -vel.y);
    dirEq = (length(dirEq) > 1e-5) ? normalize(dirEq) : vec2(0.0, -1.0);
    vec2 posA = pos + dirEq * u_eps;
    posA.x = clamp(posA.x, 0.0002, 0.9998);
    posA.y = clamp(posA.y, 0.0002, 0.9998);

    vec4 cClip = projectTile(toMerc(pos));
    vec4 aClip = projectTile(toMerc(posA));
    if (cClip.w <= 0.0) { v_speed = 0.0; v_along = 0.0; gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
    vec2 cN = cClip.xy / cClip.w;
    vec2 aN = aClip.xy / aClip.w;
    vec2 pxDir = (aN - cN) * (u_viewport * 0.5);
    vec2 sdir = (length(pxDir) > 1e-4) ? normalize(pxDir) : vec2(0.0, 1.0);   // flow dir (screen)
    vec2 perp = vec2(-sdir.y, sdir.x);

    // streak length scales with speed (windy look): base + speed fraction * scale
    float lenPx = u_halfLen * (1.0 + u_lenSpeedScale * clamp(spd / u_maxspeed, 0.0, 1.0));

    vec2 ab[6] = vec2[6](vec2(-1.0,-1.0), vec2(1.0,-1.0), vec2(-1.0,1.0),
                         vec2(-1.0, 1.0), vec2(1.0,-1.0), vec2( 1.0,1.0));
    vec2 cc = ab[corner];
    v_along = cc.x;                                   // -1 tail .. +1 head
    vec2 offPix = sdir * (cc.x * lenPx) + perp * (cc.y * u_halfThick);
    vec2 offNDC = offPix * 2.0 / u_viewport;
    gl_Position = cClip;
    gl_Position.xy += offNDC * cClip.w;
}`;

const STREAK_FS = `#version 300 es
precision highp float;
in float v_speed;
in float v_along;
out vec4 fragColor;
uniform sampler2D u_cmap;
uniform float u_maxspeed, u_alpha;
void main(){
    float t = clamp(v_speed / u_maxspeed, 0.0, 1.0);
    float grad = smoothstep(-1.0, 1.0, v_along);     // bright head, faint tail
    fragColor = vec4(texture(u_cmap, vec2(t, 0.5)).rgb, u_alpha * grad);
}`;

// ---- controller -----------------------------------------------------------

export function createWindParticleGLController(map, opts) {
    const {
        sectionKey = 'wind',
        initialConfig,
        coordinates = MERCATOR_CORNERS,
        vmax = 40.0,                                      // must match backend VMAX_WIND
        colormap = null,                                  // (cfg) -> Uint8Array(256*4)
        lodCount = null,
        staticUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile}`,
        dataUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile.replace(/\.png$/, '_data.png')}`,
        // Per-hour velocity texture, driven by the shared timeline.
        hourDataUrl = (cfg, hour, bust) => {
            const base = cfg.outfile.replace(/\.png$/, '');
            const f = String(hour).padStart(3, '0');
            return `${window.MAP_UI}/${base}_f${f}_data.png?t=${bust}`;
        },
        staticFallback = true,                            // barbs PNG when not animated / no WebGL
        particleCount = (cfg) => {
            const explicit = parseInt(cfg.particle_count, 10);
            return Math.max(256, explicit > 0 ? explicit : ((lodCount || LOD_COUNT)[lodOf(cfg)] || 65000));
        },
        // particle_speed: 0-100 -> internal advection multiplier (wind's original scale).
        speed = (cfg) => { const p = Number(cfg.particle_speed);
                           return (isFinite(p) ? Math.min(100, Math.max(0, p)) : 50) / 500; },
        // particle_alpha: 0-100 opacity.
        alpha = (cfg) => { const v = Number(cfg.particle_alpha);
                           const c = isFinite(v) ? Math.min(100, Math.max(0, v)) : 90;
                           return c / 100; },
        // trail_fade (0-100 "trail length") -> streak base half-length in px (3..15).
        streakLen = (cfg) => { const v = Number(cfg.trail_fade);
                               const c = isFinite(v) ? Math.min(100, Math.max(0, v)) : 80;
                               return 3 + (c / 100) * 12; },
        // particle_size: streak thickness in px (0.1-5).
        thickness = (cfg) => { const v = Number(cfg.particle_size);
                               return isFinite(v) ? Math.min(5, Math.max(0.1, v)) : 1.0; },
        lenSpeedScale = 1.5,                              // fast wind streaks up to 2.5x longer
        dropRate = (cfg) => (cfg.drop_rate != null ? Number(cfg.drop_rate) : 0.003),
        dropBump = (cfg) => (cfg.drop_rate_bump != null ? Number(cfg.drop_rate_bump) : 0.012),
        maxSpeedColor = (cfg) => (Number(cfg.max_speed_color) > 0 ? Number(cfg.max_speed_color) : 30.0),
        useViewDensity = true,
        eps = 0.0015,
        onMount = () => {}, onRefresh = () => {}, onUnmount = () => {},
    } = opts;

    const S_SRC = `${sectionKey}-source`, S_LYR = `${sectionKey}-layer`;
    const A_LYR = `${sectionKey}-anim-layer`;
    const isAnimated = (cfg) => !!cfg.animated;

    let mode = null, webglFailed = false, layerAdded = false;
    let unsubTimeline = null;     // timeline subscription
    let curCfgWind = null;        // latest cfg, for timeline-driven reloads
    let lastWindHour = -1;        // detect hour changes
    let lastWindBust = -1;        // detect data-refresh busts
    let glRef = null;

    let updateProg = null, screenQuad = null, vaoUpdate = null, vaoStreaks = null;
    let windTex = null, cmapTex = null;
    let stateTex = [null, null], stateFbo = [null, null], stateCur = 0;
    let RES = 256, count = 65536;
    const streakProgCache = new Map();
    let streakProgFailed = false;

    let curSpeed = 0.25, curAlpha = 0.9, curMaxSpeed = 30.0, curStreakLen = 9, curThick = 1.5,
        curDropRate = 0.003, curDropBump = 0.012;
    let curBbox = [0, 0, 1, 1];
    let windReady = false, pendingWindImg = null, pendingLut = null, pendingRebuild = false;

    const applyParams = (cfg) => {
        curSpeed = speed(cfg); curAlpha = alpha(cfg); curMaxSpeed = maxSpeedColor(cfg);
        curStreakLen = streakLen(cfg); curThick = thickness(cfg);
        curDropRate = dropRate(cfg); curDropBump = dropBump(cfg);
    };

    // equirect [0,1] respawn box. Latitude from bounds; longitude from centre + zoom
    // (antimeridian-safe), wrapping the seam when needed. Falls back to whole world.
    const viewBox = () => {
        if (!useViewDensity) return [0, 0, 1, 1];
        try {
            const b = map.getBounds();
            let n = b.getNorth(), s = b.getSouth();
            if (!Number.isFinite(n) || !Number.isFinite(s)) return [0, 0, 1, 1];
            const padLat = Math.max(0, n - s) * 0.15;
            n = Math.min(89.9, n + padLat); s = Math.max(-89.9, s - padLat);
            let yN = Math.max(0, (90 - n) / 180), yS = Math.min(1, (90 - s) / 180);
            // Keep a SMALL box at high zoom so particles stay concentrated in view, but
            // never collapse to zero — enforce a minimum height around the centre. (Bailing
            // to whole-world here is what made particles vanish above ~zoom 7.)
            const MIN_H = 0.006;
            if (yS - yN < MIN_H) {
                const cy = Math.min(1 - MIN_H * 0.5, Math.max(MIN_H * 0.5, (yN + yS) * 0.5));
                yN = cy - MIN_H * 0.5; yS = cy + MIN_H * 0.5;
            }
            const c = map.getCenter();
            const cv = map.getCanvas();
            const vw = (cv && cv.clientWidth) || 1024;
            const worldPx = 512 * Math.pow(2, map.getZoom());
            let spanLon = (vw / worldPx) * 360 * 1.4;
            if (!Number.isFinite(spanLon) || spanLon >= 350) return [0, yN, 1, yS];
            spanLon = Math.max(1.0, spanLon);                     // floor so it never collapses
            const cl = ((((c.lng + 180) % 360) + 360) % 360) / 360;
            const half = (spanLon / 360) / 2;
            let lonMin = ((((cl - half) % 1) + 1) % 1);
            let lonMax = ((((cl + half) % 1) + 1) % 1);
            return [lonMin, yN, lonMax, yS];
        } catch (_) { return [0, 0, 1, 1]; }
    };

    // ---- GL plumbing (identical patterns to the wave module) ----
    const compile = (gl, type, src) => {
        const sh = gl.createShader(type);
        gl.shaderSource(sh, src); gl.compileShader(sh);
        if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
            console.error(`[${sectionKey}] shader compile:`, gl.getShaderInfoLog(sh));
            return null;
        }
        return sh;
    };
    const linkProg = (gl, vs, fs) => {
        const v = compile(gl, gl.VERTEX_SHADER, vs), f = compile(gl, gl.FRAGMENT_SHADER, fs);
        if (!v || !f) return null;
        const p = gl.createProgram();
        gl.attachShader(p, v); gl.attachShader(p, f); gl.linkProgram(p);
        gl.deleteShader(v); gl.deleteShader(f);
        if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
            console.error(`[${sectionKey}] link:`, gl.getProgramInfoLog(p));
            return null;
        }
        return p;
    };
    const makeTex = (gl, w, h, data, filter, wrapS) => {
        const t = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrapS || gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
        return t;
    };
    const randomState = () => {
        const data = new Uint8Array(RES * RES * 4);
        for (let i = 0; i < data.length; i++) data[i] = Math.floor(Math.random() * 256);
        return data;
    };
    const buildState = (gl) => {
        for (let i = 0; i < 2; i++) {
            if (stateTex[i]) gl.deleteTexture(stateTex[i]);
            if (stateFbo[i]) gl.deleteFramebuffer(stateFbo[i]);
            stateTex[i] = makeTex(gl, RES, RES, randomState(), gl.NEAREST);
            stateFbo[i] = gl.createFramebuffer();
            gl.bindFramebuffer(gl.FRAMEBUFFER, stateFbo[i]);
            gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, stateTex[i], 0);
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        stateCur = 0;
    };
    const uploadColormapNow = (gl, lut) => {
        if (!cmapTex || !lut) return;
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut);
    };
    const uploadWindNow = (gl, img) => {
        if (windTex) gl.deleteTexture(windTex);
        windTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        windReady = true;
    };
    const loadWind = (cfg) => {
        const snap = timeline.get();
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => { pendingWindImg = img; map.triggerRepaint(); };
        img.onerror = () => console.warn(`[${sectionKey}] velocity texture not ready (f${String(snap.hour).padStart(3,'0')})`);
        img.src = hourDataUrl(cfg, snap.hour, snap.refreshEpoch);
    };

    const getStreakProg = (gl, shaderData) => {
        const key = shaderData.variantName || '__default__';
        if (streakProgCache.has(key)) return streakProgCache.get(key);
        if (streakProgFailed) return null;
        const vs = `#version 300 es\n${shaderData.vertexShaderPrelude}\n${shaderData.define}\n${STREAK_VS_BODY}`;
        const prog = linkProg(gl, vs, STREAK_FS);
        if (!prog) { streakProgFailed = true; return null; }
        streakProgCache.set(key, prog);
        return prog;
    };

    const advect = (gl) => {
        const prevFbo = gl.getParameter(gl.FRAMEBUFFER_BINDING);
        const prevVp = gl.getParameter(gl.VIEWPORT);
        gl.bindVertexArray(vaoUpdate);
        gl.bindFramebuffer(gl.FRAMEBUFFER, stateFbo[1 - stateCur]);
        gl.viewport(0, 0, RES, RES);
        gl.disable(gl.BLEND);
        gl.useProgram(updateProg);
        const u = (n) => gl.getUniformLocation(updateProg, n);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, stateTex[stateCur]);
        gl.uniform1i(u('u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.uniform1i(u('u_wind'), 1);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform1f(u('u_speed'), curSpeed);
        gl.uniform1f(u('u_dropRate'), curDropRate);
        gl.uniform1f(u('u_dropBump'), curDropBump);
        gl.uniform1f(u('u_dropSpeed'), 10.0);
        gl.uniform1f(u('u_landReset'), 0.0);              // wind covers land too
        gl.uniform4f(u('u_bboxPos'), curBbox[0], curBbox[1], curBbox[2], curBbox[3]);
        gl.uniform1f(u('u_seed'), Math.random());
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        stateCur = 1 - stateCur;
        gl.bindFramebuffer(gl.FRAMEBUFFER, prevFbo);
        gl.viewport(prevVp[0], prevVp[1], prevVp[2], prevVp[3]);
    };

    const drawStreaks = (gl, args) => {
        const prog = getStreakProg(gl, args.shaderData);
        if (!prog) return;
        gl.useProgram(prog);
        gl.bindVertexArray(vaoStreaks);
        const u = (n) => gl.getUniformLocation(prog, n);
        const pd = args.defaultProjectionData;
        gl.uniformMatrix4fv(u('u_projection_matrix'), false, pd.mainMatrix);
        gl.uniformMatrix4fv(u('u_projection_fallback_matrix'), false, pd.fallbackMatrix);
        gl.uniform4f(u('u_projection_clipping_plane'),
            pd.clippingPlane[0], pd.clippingPlane[1], pd.clippingPlane[2], pd.clippingPlane[3]);
        gl.uniform1f(u('u_projection_transition'), pd.projectionTransition);
        gl.uniform4f(u('u_projection_tile_mercator_coords'),
            pd.tileMercatorCoords[0], pd.tileMercatorCoords[1],
            pd.tileMercatorCoords[2], pd.tileMercatorCoords[3]);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, stateTex[stateCur]);
        gl.uniform1i(u('u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.uniform1i(u('u_wind'), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(u('u_cmap'), 2);
        gl.uniform1f(u('u_res'), RES);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform2f(u('u_viewport'), gl.drawingBufferWidth, gl.drawingBufferHeight);
        gl.uniform1f(u('u_halfLen'), curStreakLen);
        gl.uniform1f(u('u_halfThick'), Math.max(0.5, curThick));
        gl.uniform1f(u('u_eps'), eps);
        gl.uniform1f(u('u_lenSpeedScale'), lenSpeedScale);
        gl.uniform1f(u('u_maxspeed'), curMaxSpeed);
        gl.uniform1f(u('u_alpha'), curAlpha);
        gl.disable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.drawArrays(gl.TRIANGLES, 0, count * 6);
    };

    const makeLayer = (cfg) => ({
        id: A_LYR,
        type: 'custom',
        renderingMode: '2d',
        onAdd(m, gl) {
            glRef = gl;
            updateProg = linkProg(gl, QUAD_VS, UPDATE_FS);
            if (!updateProg) { webglFailed = true; return; }
            screenQuad = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, screenQuad);
            gl.bufferData(gl.ARRAY_BUFFER,
                new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]), gl.STATIC_DRAW);
            vaoUpdate = gl.createVertexArray();
            gl.bindVertexArray(vaoUpdate);
            gl.bindBuffer(gl.ARRAY_BUFFER, screenQuad);
            const loc = gl.getAttribLocation(updateProg, 'a_pos');
            gl.enableVertexAttribArray(loc);
            gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
            vaoStreaks = gl.createVertexArray();
            gl.bindVertexArray(null);

            cmapTex = makeTex(gl, 1, 1, new Uint8Array([255, 255, 255, 255]), gl.LINEAR);
            buildState(gl);
            applyParams(cfg);
            if (colormap) uploadColormapNow(gl, colormap(cfg));
            loadWind(cfg);
            map.triggerRepaint();
        },
        render(gl, args) {
            if (webglFailed || !updateProg) return;
            if (pendingRebuild) { buildState(gl); pendingRebuild = false; }
            if (pendingWindImg) { uploadWindNow(gl, pendingWindImg); pendingWindImg = null; }
            if (pendingLut) { uploadColormapNow(gl, pendingLut); pendingLut = null; }
            if (!windReady || !windTex) { map.triggerRepaint(); return; }
            curBbox = viewBox();
            advect(gl);
            drawStreaks(gl, args);
            gl.bindVertexArray(null);
            map.triggerRepaint();
        },
        onRemove(m, gl) {
            streakProgCache.forEach((p) => gl.deleteProgram(p));
            streakProgCache.clear(); streakProgFailed = false;
            [windTex, cmapTex, ...stateTex].forEach((t) => t && gl.deleteTexture(t));
            [...stateFbo].forEach((f) => f && gl.deleteFramebuffer(f));
            if (screenQuad) gl.deleteBuffer(screenQuad);
            if (vaoUpdate) gl.deleteVertexArray(vaoUpdate);
            if (vaoStreaks) gl.deleteVertexArray(vaoStreaks);
            if (updateProg) gl.deleteProgram(updateProg);
            windTex = cmapTex = updateProg = screenQuad = vaoUpdate = vaoStreaks = null;
            stateTex = [null, null]; stateFbo = [null, null];
            windReady = false; pendingWindImg = pendingLut = null; pendingRebuild = false;
            glRef = null;
        },
    });

    // ---- static barbs fallback (unchanged behaviour) ----
    const mountStatic = (cfg) => {
        if (!staticFallback || map.getSource(S_SRC)) return;
        map.addSource(S_SRC, { type: 'image', url: `${staticUrl(cfg)}?t=${Date.now()}`, coordinates });
        map.addLayer({ id: S_LYR, type: 'raster', source: S_SRC, paint: { 'raster-opacity': 0.85, 'raster-fade-duration': 0 } });
    };
    const refreshStatic = (cfg) => {
        if (!staticFallback) return;
        const s = map.getSource(S_SRC);
        if (s) s.updateImage({ url: `${staticUrl(cfg)}?t=${Date.now()}` });
    };
    const unmountStatic = () => {
        if (map.getLayer(S_LYR)) map.removeLayer(S_LYR);
        if (map.getSource(S_SRC)) map.removeSource(S_SRC);
    };

    // ---- animated (custom WebGL layer) ----
    const sizeFor = (cfg) => {
        const c = particleCount(cfg);
        const r = Math.max(16, Math.round(Math.sqrt(c)));
        return { RES: r, count: r * r };
    };
    const mountAnimated = (cfg) => {
        if (layerAdded || map.getLayer(A_LYR)) return;
        const sz = sizeFor(cfg); RES = sz.RES; count = sz.count;
        webglFailed = false; streakProgFailed = false; windReady = false;
        curCfgWind = cfg;
        map.addLayer(makeLayer(cfg));
        layerAdded = true;
        if (webglFailed) {                                 // onAdd failed to compile -> fall back
            unmountAnimated();
            if (staticFallback) { mountStatic(cfg); mode = 'static'; } else { mode = 'none'; }
            return;
        }
        // Drive the velocity field from the shared timeline: reload the wind texture
        // whenever the forecast hour changes or a data refresh busts the cache. The
        // particles keep flowing; only the underlying field swaps (hard cut per hour).
        const snap0 = timeline.get();
        lastWindHour = snap0.hour; lastWindBust = snap0.refreshEpoch;
        unsubTimeline = timeline.subscribe((snap) => {
            if (snap.hour !== lastWindHour || snap.refreshEpoch !== lastWindBust) {
                lastWindHour = snap.hour; lastWindBust = snap.refreshEpoch;
                if (curCfgWind) loadWind(curCfgWind);
            }
        });
        scrubber.layerActivated();
        onMount(cfg);
    };
    const refreshAnimated = (cfg) => {
        const sz = sizeFor(cfg);
        if (sz.RES !== RES) { RES = sz.RES; count = sz.count; pendingRebuild = true; }
        curCfgWind = cfg;
        applyParams(cfg);
        if (colormap) pendingLut = colormap(cfg);
        loadWind(cfg);
        map.triggerRepaint();
        onRefresh(cfg);
    };
    const unmountAnimated = () => {
        if (unsubTimeline) { unsubTimeline(); unsubTimeline = null; }
        if (layerAdded) scrubber.layerDeactivated();
        if (map.getLayer(A_LYR)) map.removeLayer(A_LYR);   // triggers onRemove cleanup
        layerAdded = false;
        onUnmount();
    };

    // ---- dispatch ----
    const wanted = (cfg) => (isAnimated(cfg) && !webglFailed) ? 'animated' : (staticFallback ? 'static' : 'none');
    const switchTo = (want, cfg) => {
        if (want === 'animated') { unmountStatic(); mountAnimated(cfg); }
        else if (want === 'static') { unmountAnimated(); mountStatic(cfg); }
        else { unmountAnimated(); unmountStatic(); }
        mode = want;
    };
    const mount = (cfg) => {
        mode = wanted(cfg);
        if (mode === 'animated') mountAnimated(cfg);
        else if (mode === 'static') mountStatic(cfg);
    };
    const refresh = (cfg) => {
        const want = wanted(cfg);
        if (want !== mode) switchTo(want, cfg);
        else if (mode === 'animated') refreshAnimated(cfg);
        else if (mode === 'static') refreshStatic(cfg);
    };
    const unmount = () => {
        if (mode === 'static') unmountStatic();
        else if (mode === 'animated') unmountAnimated();
        mode = null;
    };

    return {
        mount, refresh, unmount,
        imageUrl: (cfg) => (isAnimated(cfg) && !webglFailed) || !staticFallback
            ? hourDataUrl(cfg, timeline.get().hour, timeline.get().refreshEpoch) : staticUrl(cfg),
    };
}

// Backwards-compatible wrapper (wind): build the controller and drive it from the shared
// liveLayerSync — same shape as the old createParticleWindLayer, so wind.js only swaps
// the import.
export function createWindParticleGLLayer(map, opts) {
    const c = createWindParticleGLController(map, opts);
    liveLayerSync(map, {
        sectionKey: opts.sectionKey ?? 'wind',
        initialConfig: opts.initialConfig,
        mount: c.mount, refresh: c.refresh, unmount: c.unmount,
        imageUrl: c.imageUrl,
        refreshMs: opts.refreshMs, syncMs: opts.syncMs,
    });
    return c;
}