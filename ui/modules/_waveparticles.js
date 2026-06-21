/**
 * Wave swell bars as a MapLibre v5 CUSTOM WEBGL LAYER.
 *
 * Unlike the wind engine (_windparticles.js), which renders particles into an
 * offscreen canvas handed to MapLibre as an image/canvas source, this module draws
 * the bars DIRECTLY into the map's own GL context every frame using MapLibre's
 * `projectTile` projection function. That means the bars are rasterised at screen
 * resolution and follow the globe (or mercator) projection exactly — so they stay
 * crisp and keep a constant on-screen size as you zoom, instead of being a fixed
 * image stretched over the sphere. (See MapLibre's "custom layer on a globe" example.)
 *
 * Pipeline each frame (render):
 *   1. Advection pass — GPU ping-pong in equirectangular [0,1] space (REUSES the wind
 *      engine's UPDATE_FS verbatim): particles flow along the swell-velocity texture
 *      (data/waves_data.png), wrap the dateline, and respawn (preferentially inside the
 *      current view box, for density at high zoom). Rendered to our own FBO.
 *   2. Bar pass — for each particle we project ONLY its centre through projectTile, then
 *      build a thin oriented quad in screen space around that projected point (so no
 *      triangle is projected across the globe's curvature → no horizon clipping). The bar
 *      lies perpendicular to the swell direction (windy.com look), coloured by wave height.
 *
 * Drop-in for waves.js: createWaveParticleController(map, opts) -> { mount, refresh, unmount }.
 * _windparticles.js (wind) is intentionally left untouched.
 */

const lodOf = (cfg) => { const n = parseInt(cfg.level_of_detail, 10); return (n === 1 || n === 3) ? n : 2; };
const LOD_COUNT = { 1: 4000, 2: 9000, 3: 18000 };
const LAT_MAX = 1.4844222297453324;   // mercator clamp, 85.0511 deg in radians

// ---- shaders --------------------------------------------------------------

const QUAD_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() { v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;

// 16-bit position packing across two 8-bit channels — keeps state in a plain RGBA8
// texture (no float-texture extension needed). Identical to the wind engine.
const PACK = `
vec2 packPos(float x){ float e = floor(clamp(x,0.0,1.0)*65535.0 + 0.5);
    float hi = floor(e/256.0); return vec2(hi, e - hi*256.0)/255.0; }
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
vec4 encodePos(vec2 p){ return vec4(packPos(p.x), packPos(p.y)); }
float rand(vec2 co){ return fract(sin(dot(co, vec2(12.9898,78.233))) * 43758.5453); }`;

// Advection in equirectangular [0,1] space (pos.x = lon 0..1, pos.y = 0 at +90 .. 1 at -90).
// Samples the swell-velocity texture, steps, wraps longitude, respawns inside the view box.
// This is the wind engine's UPDATE_FS unchanged — projection is irrelevant to advection.
const UPDATE_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_particles;
uniform sampler2D u_wind;
uniform float u_vmax, u_speed, u_dropRate, u_dropBump, u_dropSpeed, u_seed, u_landReset;
uniform vec4 u_bboxPos;   // (lonMin, yMin, lonMax, yMax) in [0,1]; world = (0,0,1,1)
const float PI = 3.141592653589793;
const float STEP = 0.0005;
${PACK}
void main(){
    vec2 pos = decodePos(texture(u_particles, v_uv));
    float lat = (0.5 - pos.y) * PI;
    vec4 w = texture(u_wind, pos);
    vec2 vel = (w.a < 0.5) ? vec2(0.0) : (w.rg * (2.0*u_vmax) - u_vmax);   // m/s (east,north)
    float coslat = max(cos(lat), 0.05);
    // The swell direction is encoded as the direction waves come FROM; they propagate
    // the opposite way, so we advect AGAINST the encoded velocity.
    vec2 d = vec2(-vel.x / coslat * 0.5, vel.y) * (u_speed * STEP);
    vec2 npos = pos + d;
    npos.x = fract(npos.x + 1.0);                       // wrap longitude seam
    float speed = length(vel);
    float drop = u_dropRate + (1.0 - clamp(speed/u_dropSpeed, 0.0, 1.0)) * u_dropBump;
    vec2 seed = (pos + v_uv) * (u_seed + 1.0);
    vec2 bmin = u_bboxPos.xy, bmax = u_bboxPos.zw;
    // The longitude box may WRAP the antimeridian (bmin.x > bmax.x): then it covers
    // the two arcs [bmin.x, 1] U [0, bmax.x]. Latitude never wraps.
    bool lonWrap = bmin.x > bmax.x;
    float rlon;
    if (!lonWrap) {
        rlon = bmin.x + rand(seed + 1.3) * (bmax.x - bmin.x);
    } else {
        float wlo = 1.0 - bmin.x, whi = bmax.x;        // widths of the two arcs
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

// Bar vertex shader BODY (everything after the version line). The MapLibre projection
// prelude + define are prepended at compile time, giving us projectTile(vec2 merc_0to1).
// We project the bar centre (and a neighbour for orientation) and build the quad in
// screen space, so curvature/horizon clipping never splits a bar.
const BAR_VS_BODY = `
precision highp float;
uniform sampler2D u_particles;
uniform sampler2D u_wind;
uniform float u_res, u_vmax, u_halfLen, u_halfThick, u_eps;
uniform vec2 u_viewport;            // drawing-buffer size in px
out float v_speed;
// NOTE: the injected MapLibre projection prelude already defines PI (and may define
// other common names), so we prefix ours to avoid 'redefinition' compile errors.
const float WV_PI = 3.141592653589793;
const float WV_LATMAX = 1.4844222297453324;
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
// equirect [0,1] -> web-mercator [0,1] (projectTile's expected input; (0,0)=top-left=-180/+85)
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
    // No swell over land/missing -> collapse the bar off-screen.
    if (w.a < 0.5) { v_speed = 0.0; gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
    vec2 vel = w.rg * (2.0*u_vmax) - u_vmax;
    v_speed = length(vel);

    float lat = (0.5 - pos.y) * WV_PI;
    float coslat = max(cos(lat), 0.05);
    vec2 dirEq = vec2(vel.x / coslat, -vel.y);
    dirEq = (length(dirEq) > 1e-5) ? normalize(dirEq) : vec2(0.0, -1.0);
    vec2 posA = pos + dirEq * u_eps;                       // a touch ahead, for orientation
    posA.x = clamp(posA.x, 0.0002, 0.9998);
    posA.y = clamp(posA.y, 0.0002, 0.9998);

    vec4 cClip = projectTile(toMerc(pos));                 // bar centre in clip space
    vec4 aClip = projectTile(toMerc(posA));
    if (cClip.w <= 0.0) { v_speed = 0.0; gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
    vec2 cN = cClip.xy / cClip.w;
    vec2 aN = aClip.xy / aClip.w;
    vec2 pxDir = (aN - cN) * (u_viewport * 0.5);           // NDC delta -> pixels (swell dir)
    vec2 sdir = (length(pxDir) > 1e-4) ? normalize(pxDir) : vec2(0.0, 1.0);
    vec2 perp = vec2(-sdir.y, sdir.x);                     // bar long axis = perpendicular

    vec2 ab[6] = vec2[6](vec2(-1.0,-1.0), vec2(1.0,-1.0), vec2(-1.0,1.0),
                         vec2(-1.0, 1.0), vec2(1.0,-1.0), vec2( 1.0,1.0));
    vec2 cc = ab[corner];
    vec2 offPix = perp * (cc.x * u_halfLen) + sdir * (cc.y * u_halfThick);
    vec2 offNDC = offPix * 2.0 / u_viewport;
    gl_Position = cClip;
    gl_Position.xy += offNDC * cClip.w;                    // perspective-correct screen offset
}`;

const BAR_FS = `#version 300 es
precision highp float;
in float v_speed;
out vec4 fragColor;
uniform sampler2D u_cmap;
uniform float u_maxspeed, u_alpha;
void main(){
    float t = clamp(v_speed / u_maxspeed, 0.0, 1.0);
    fragColor = vec4(texture(u_cmap, vec2(t, 0.5)).rgb, u_alpha);
}`;

// ---- controller -----------------------------------------------------------

export function createWaveParticleController(map, opts) {
    const {
        sectionKey = 'waves',
        initialConfig,
        vmax = 8.0,                                       // must match backend VMAX_WAVES
        colormap = null,                                  // (cfg) -> Uint8Array(256*4)
        lodCount = null,                                  // { 1, 2, 3 } -> particle counts
        dataUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile.replace(/\.png$/, '_data.png')}`,
        // resolvers over the layer config
        particleCount = (cfg) => {
            const explicit = parseInt(cfg.particle_count, 10);
            return Math.max(256, explicit > 0 ? explicit : ((lodCount || LOD_COUNT)[lodOf(cfg)] || 9000));
        },
        // particle_speed: 1-100 slider. Swell drifts slowly, so the usable range is the
        // low end — we map the whole 1..100 onto what used to be ~1..10 (i.e. /2000, not
        // /200), so 100 now matches the old setting of 10 and the slider isn't "all silly".
        speed = (cfg) => { const p = Number(cfg.particle_speed);
                           return (isFinite(p) ? Math.min(100, Math.max(0, p)) : 50) / 2000; },
        // particle_alpha: 0-100 opacity.
        alpha = (cfg) => { const v = Number(cfg.particle_alpha);
                           const c = isFinite(v) ? Math.min(100, Math.max(0, v)) : 70;
                           return c / 100; },
        // bar_length: half-length of a bar in screen px (1-20), along the perpendicular.
        barLength = (cfg) => { const v = Number(cfg.bar_length);
                               return isFinite(v) ? Math.min(20, Math.max(1, v)) : 7; },
        // particle_size: bar thickness in px (0.5-5).
        thickness = (cfg) => { const v = Number(cfg.particle_size);
                               return isFinite(v) ? Math.min(5, Math.max(0.5, v)) : 1.5; },
        dropRate = (cfg) => (cfg.drop_rate != null ? Number(cfg.drop_rate) : 0.003),
        dropBump = (cfg) => (cfg.drop_rate_bump != null ? Number(cfg.drop_rate_bump) : 0.012),
        maxSpeedColor = (cfg) => (Number(cfg.max_speed_color) > 0 ? Number(cfg.max_speed_color) : vmax),
        // density: respawn particles inside the current view box (true) or across the
        // whole world (false). View density concentrates bars where you're looking.
        useViewDensity = true,
        eps = 0.0015,                                     // orientation probe distance (equirect)
        onMount = () => {}, onRefresh = () => {}, onUnmount = () => {},
    } = opts;

    const A_LYR = `${sectionKey}-anim-layer`;
    // Particles are a visualisation methodology, not forecast stepping: this layer
    // always renders its particles when enabled (gated only by WebGL availability),
    // independent of the global [animation].forecast_stepping switch.
    const isAnimated = () => true;

    let mode = null;                                      // 'animated' | 'none' | null
    let glRef = null, layerAdded = false, webglFailed = false;

    // GL objects (created in onAdd)
    let updateProg = null, screenQuad = null, vaoUpdate = null, vaoBars = null;
    let windTex = null, cmapTex = null;
    let stateTex = [null, null], stateFbo = [null, null], stateCur = 0;
    let RES = 64, count = 4096;
    const barProgCache = new Map();                       // variantName -> program
    let barProgFailed = false;

    // live params + pending-work flags (applied at the top of render, where the GL
    // context is guaranteed current)
    let curSpeed = 0.25, curAlpha = 0.7, curMaxSpeed = vmax, curBarLen = 7, curThick = 1.5,
        curDropRate = 0.003, curDropBump = 0.012;
    let curBbox = [0, 0, 1, 1];
    let windReady = false, pendingWindImg = null, pendingLut = null, pendingRebuild = false;
    let lastCfg = initialConfig;

    const applyParams = (cfg) => {
        curSpeed = speed(cfg); curAlpha = alpha(cfg); curMaxSpeed = maxSpeedColor(cfg);
        curBarLen = barLength(cfg); curThick = thickness(cfg);
        curDropRate = dropRate(cfg); curDropBump = dropBump(cfg);
    };

    // equirect [0,1] view box for respawn density. Latitude comes from the map bounds
    // (always reliable). Longitude comes from CENTRE + ZOOM, not getBounds(), because
    // near the antimeridian getBounds() reports a one-sided range — which used to make
    // bars populate only one hemisphere. The lon box may wrap the seam (lonMin > lonMax);
    // the advection shader handles that. Any oddity falls back to the whole world.
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
            const worldPx = 512 * Math.pow(2, map.getZoom());     // map width in px (512 tiles)
            let spanLon = (vw / worldPx) * 360 * 1.4;             // mercator approx + 40% pad
            if (!Number.isFinite(spanLon) || spanLon >= 350) return [0, yN, 1, yS];
            spanLon = Math.max(1.0, spanLon);                     // floor so it never collapses
            const cl = ((((c.lng + 180) % 360) + 360) % 360) / 360;   // centre lon -> [0,1)
            const half = (spanLon / 360) / 2;
            let lonMin = ((((cl - half) % 1) + 1) % 1);
            let lonMax = ((((cl + half) % 1) + 1) % 1);
            return [lonMin, yN, lonMax, yS];   // lonMin > lonMax => wraps the seam (handled)
        } catch (_) { return [0, 0, 1, 1]; }
    };

    // ---- GL plumbing ----
    const compile = (gl, type, src) => {
        const sh = gl.createShader(type);
        gl.shaderSource(sh, src); gl.compileShader(sh);
        if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
            console.error(`[${sectionKey}] shader compile:`, gl.getShaderInfoLog(sh));
            return null;
        }
        return sh;
    };
    const link = (gl, vs, fs) => {
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
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);     // wrap longitude
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        windReady = true;
    };
    const loadWind = (cfg) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => { pendingWindImg = img; map.triggerRepaint(); };
        img.onerror = () => console.warn(`[${sectionKey}] velocity texture not ready: ${dataUrl(cfg)}`);
        img.src = `${dataUrl(cfg)}?t=${Date.now()}`;
    };

    const getBarProg = (gl, shaderData) => {
        const key = shaderData.variantName || '__default__';
        if (barProgCache.has(key)) return barProgCache.get(key);
        if (barProgFailed) return null;
        const vs = `#version 300 es\n${shaderData.vertexShaderPrelude}\n${shaderData.define}\n${BAR_VS_BODY}`;
        const prog = link(gl, vs, BAR_FS);
        if (!prog) { barProgFailed = true; return null; }
        barProgCache.set(key, prog);
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
        gl.uniform1f(u('u_landReset'), 1.0);
        gl.uniform4f(u('u_bboxPos'), curBbox[0], curBbox[1], curBbox[2], curBbox[3]);
        gl.uniform1f(u('u_seed'), Math.random());
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        stateCur = 1 - stateCur;
        // restore MapLibre's render target + viewport before we draw into it
        gl.bindFramebuffer(gl.FRAMEBUFFER, prevFbo);
        gl.viewport(prevVp[0], prevVp[1], prevVp[2], prevVp[3]);
    };

    const drawBars = (gl, args) => {
        const prog = getBarProg(gl, args.shaderData);
        if (!prog) return;
        gl.useProgram(prog);
        gl.bindVertexArray(vaoBars);
        const u = (n) => gl.getUniformLocation(prog, n);
        // MapLibre projection uniforms (some may be inactive per variant -> null -> no-op)
        const pd = args.defaultProjectionData;
        gl.uniformMatrix4fv(u('u_projection_matrix'), false, pd.mainMatrix);
        gl.uniformMatrix4fv(u('u_projection_fallback_matrix'), false, pd.fallbackMatrix);
        gl.uniform4f(u('u_projection_clipping_plane'),
            pd.clippingPlane[0], pd.clippingPlane[1], pd.clippingPlane[2], pd.clippingPlane[3]);
        gl.uniform1f(u('u_projection_transition'), pd.projectionTransition);
        gl.uniform4f(u('u_projection_tile_mercator_coords'),
            pd.tileMercatorCoords[0], pd.tileMercatorCoords[1],
            pd.tileMercatorCoords[2], pd.tileMercatorCoords[3]);
        // our uniforms
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, stateTex[stateCur]);
        gl.uniform1i(u('u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.uniform1i(u('u_wind'), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(u('u_cmap'), 2);
        gl.uniform1f(u('u_res'), RES);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform2f(u('u_viewport'), gl.drawingBufferWidth, gl.drawingBufferHeight);
        gl.uniform1f(u('u_halfLen'), curBarLen);
        gl.uniform1f(u('u_halfThick'), Math.max(0.5, curThick));
        gl.uniform1f(u('u_eps'), eps);
        gl.uniform1f(u('u_maxspeed'), curMaxSpeed);
        gl.uniform1f(u('u_alpha'), curAlpha);
        gl.disable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.drawArrays(gl.TRIANGLES, 0, count * 6);        // 2 tris/particle, no attributes
    };

    // ---- the custom layer ----
    const makeLayer = (cfg) => ({
        id: A_LYR,
        type: 'custom',
        renderingMode: '2d',
        onAdd(m, gl) {
            glRef = gl;
            updateProg = link(gl, QUAD_VS, UPDATE_FS);
            if (!updateProg) { webglFailed = true; return; }
            screenQuad = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, screenQuad);
            gl.bufferData(gl.ARRAY_BUFFER,
                new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]), gl.STATIC_DRAW);
            // VAO for the advection quad (a_pos); a separate empty VAO for the attribute-less bars
            vaoUpdate = gl.createVertexArray();
            gl.bindVertexArray(vaoUpdate);
            gl.bindBuffer(gl.ARRAY_BUFFER, screenQuad);
            const loc = gl.getAttribLocation(updateProg, 'a_pos');
            gl.enableVertexAttribArray(loc);
            gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
            vaoBars = gl.createVertexArray();
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
            // apply any work queued from refresh() now that the context is current
            if (pendingRebuild) { buildState(gl); pendingRebuild = false; }
            if (pendingWindImg) { uploadWindNow(gl, pendingWindImg); pendingWindImg = null; }
            if (pendingLut) { uploadColormapNow(gl, pendingLut); pendingLut = null; }
            if (!windReady || !windTex) { map.triggerRepaint(); return; }

            curBbox = viewBox();
            advect(gl);
            drawBars(gl, args);
            gl.bindVertexArray(null);
            map.triggerRepaint();                          // keep the animation running
        },
        onRemove(m, gl) {
            barProgCache.forEach((p) => gl.deleteProgram(p));
            barProgCache.clear(); barProgFailed = false;
            [windTex, cmapTex, ...stateTex].forEach((t) => t && gl.deleteTexture(t));
            [...stateFbo].forEach((f) => f && gl.deleteFramebuffer(f));
            if (screenQuad) gl.deleteBuffer(screenQuad);
            if (vaoUpdate) gl.deleteVertexArray(vaoUpdate);
            if (vaoBars) gl.deleteVertexArray(vaoBars);
            if (updateProg) gl.deleteProgram(updateProg);
            windTex = cmapTex = updateProg = screenQuad = vaoUpdate = vaoBars = null;
            stateTex = [null, null]; stateFbo = [null, null];
            windReady = false; pendingWindImg = pendingLut = null; pendingRebuild = false;
            glRef = null;
        },
    });

    // ---- public API (drop-in for the wind controller) ----
    const sizeFor = (cfg) => {
        const c = particleCount(cfg);
        const r = Math.max(16, Math.round(Math.sqrt(c)));
        return { RES: r, count: r * r };
    };

    const mountAnimated = (cfg) => {
        if (layerAdded || map.getLayer(A_LYR)) return;
        const sz = sizeFor(cfg); RES = sz.RES; count = sz.count;
        webglFailed = false; barProgFailed = false; windReady = false;
        lastCfg = cfg;
        map.addLayer(makeLayer(cfg));
        layerAdded = true;
        onMount(cfg);
    };
    const unmountAnimated = () => {
        if (map.getLayer(A_LYR)) map.removeLayer(A_LYR);   // triggers onRemove cleanup
        layerAdded = false;
        onUnmount();
    };
    const refreshAnimated = (cfg) => {
        lastCfg = cfg;
        const sz = sizeFor(cfg);
        if (sz.RES !== RES) { RES = sz.RES; count = sz.count; pendingRebuild = true; }
        applyParams(cfg);
        if (colormap) pendingLut = colormap(cfg);
        loadWind(cfg);   // pick up regenerated swell data
        map.triggerRepaint();
        onRefresh(cfg);
    };

    const wanted = (cfg) => (isAnimated() && !webglFailed) ? 'animated' : 'none';
    const mount = (cfg) => {
        mode = wanted(cfg);
        if (mode === 'animated') mountAnimated(cfg);
    };
    const refresh = (cfg) => {
        const want = wanted(cfg);
        if (want !== mode) {
            if (want === 'animated') mountAnimated(cfg); else unmountAnimated();
            mode = want;
        } else if (mode === 'animated') {
            refreshAnimated(cfg);
        }
    };
    const unmount = () => { if (mode === 'animated') unmountAnimated(); mode = null; };

    return { mount, refresh, unmount, imageUrl: (cfg) => dataUrl(cfg) };
}