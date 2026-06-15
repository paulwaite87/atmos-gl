import { liveLayerSync } from './_refresh.js';
import { timeline } from './timeline.js';
import { scrubber } from './scrubber.js';

/**
 * Ocean-current FLOWING TRAIL particles as a MapLibre v5 CUSTOM WEBGL LAYER.
 *
 * Unlike the wind/wave engine (one stiff oriented quad per particle = "bars"), this
 * draws each particle as a short fading, tapering POLYLINE of its recent positions —
 * so it traces the curved streamline it has flowed along, giving the smooth
 * "flowing ribbon" look. Particles are advected on the GPU along the u/v texture
 * exactly like wind; the new part is a HISTORY CHAIN of state textures (the last
 * TRAIL_LEN positions) and a trail vertex shader that builds screen-space ribbon
 * segments between consecutive history positions, projected through MapLibre's
 * projectTile (so trails follow the globe and stay crisp at any zoom).
 *
 * Isolated module — wind (_windparticles_gl.js) and waves are untouched.
 *
 * createCurrentParticleGLLayer(map, opts) — opts mirror the wind layer where useful:
 *   sectionKey, initialConfig, vmax, colormap, hourDataUrl, maxSpeedColor, landReset,
 *   plus trail tunables (trail_len via config, particle_count, particle_speed, etc.).
 */

const TRAIL_LEN = 14;            // history positions per trail (medium ribbons)
const LOD_COUNT = { 1: 4000, 2: 9000, 3: 18000 };
const LAT_MAX = 1.4844222297453324;   // mercator clamp (85.0511 deg, radians)

const lodOf = (cfg) => { const n = parseInt(cfg.level_of_detail, 10); return (n === 1 || n === 3) ? n : 2; };

// 16-bit position packing across two 8-bit channels (no float-texture extension).
const PACK = `
vec2 packPos(float x){ float e = floor(clamp(x,0.0,1.0)*65535.0 + 0.5);
  return vec2(floor(e/256.0)/255.0, mod(e,256.0)/255.0); }
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
vec4 encodePos(vec2 p){ return vec4(packPos(p.x), packPos(p.y)); }
float rand(vec2 co){ return fract(sin(dot(co, vec2(12.9898,78.233))) * 43758.5453); }`;

const QUAD_VS = `#version 300 es
in vec2 a_pos; out vec2 v_uv;
void main(){ v_uv = a_pos; gl_Position = vec4(a_pos*2.0-1.0, 0.0, 1.0); }`;

// Advection: head position step along u/v (reuses the wind engine's logic verbatim,
// including landReset so trails die on land).
const UPDATE_FS = `#version 300 es
precision highp float;
in vec2 v_uv; out vec4 fragColor;
uniform sampler2D u_particles;     // current head positions
uniform sampler2D u_vel;
uniform float u_vmax, u_speed, u_dropRate, u_dropBump, u_dropSpeed, u_seed, u_landReset;
uniform vec4 u_bboxPos;
const float PI = 3.141592653589793;
const float STEP = 0.0005;
${PACK}
void main(){
    vec2 pos = decodePos(texture(u_particles, v_uv));
    float lat = (0.5 - pos.y) * PI;
    vec4 w = texture(u_vel, pos);
    vec2 vel = (w.a < 0.5) ? vec2(0.0) : (w.rg * (2.0*u_vmax) - u_vmax);
    float coslat = max(cos(lat), 0.05);
    vec2 d = vec2(vel.x / coslat * 0.5, -vel.y) * (u_speed * STEP);
    vec2 npos = pos + d;
    npos.x = fract(npos.x + 1.0);
    float speed = length(vel);
    // Speed-weighted lifetime: slow water drops fast (sparse), fast water persists
    // (tight bright ribbons on strong currents).
    float drop = u_dropRate + (1.0 - clamp(speed/u_dropSpeed, 0.0, 1.0)) * u_dropBump;
    vec2 seed = (pos + v_uv) * (u_seed + 1.0);
    vec2 bmin = u_bboxPos.xy, bmax = u_bboxPos.zw;
    bool lonWrap = bmin.x > bmax.x;
    float rlon;
    if (!lonWrap) { rlon = bmin.x + rand(seed + 1.3) * (bmax.x - bmin.x); }
    else { float wlo = 1.0 - bmin.x, whi = bmax.x; float r = rand(seed + 1.3) * (wlo + whi);
           rlon = (r < wlo) ? (bmin.x + r) : (r - wlo); }
    float rlat = bmin.y + rand(seed + 2.7) * (bmax.y - bmin.y);
    vec2 randPos = vec2(rlon, rlat);
    bool lonOut = lonWrap ? (npos.x < bmin.x && npos.x > bmax.x)
                          : (npos.x < bmin.x || npos.x > bmax.x);
    bool outside = lonOut || (npos.y < bmin.y) || (npos.y > bmax.y);
    bool reset = (rand(seed) < drop) || (npos.y <= 0.0) || (npos.y >= 1.0)
                 || (u_landReset > 0.5 && w.a < 0.5) || outside;
    fragColor = encodePos(reset ? randPos : npos);
}`;

// Trail vertex shader BODY (MapLibre projection prelude + define prepended at link).
// One ribbon SEGMENT per (particle, history-step). gl_VertexID layout: 6 verts per
// segment, (TRAIL_LEN-1) segments per particle. Segment k connects history slot k
// (older, tail side) to k+1 (newer, head side). Reads two history textures bound to
// units, projects both endpoints via projectTile(toMerc()), builds a screen-space
// quad, tapered + faded toward the tail.
const TRAIL_VS_BODY = `
precision highp float;
uniform sampler2D u_hist[${TRAIL_LEN}];   // ring of history position textures (0=newest)
uniform sampler2D u_vel;
uniform float u_res, u_vmax, u_halfThick, u_maxspeed, u_alpha, u_trailLen;
uniform vec2 u_viewport;
out float v_speed;
out float v_t;            // 0 at tail .. 1 at head (for fade/taper in FS)
const float CP_PI = 3.141592653589793;
const float CP_LATMAX = 1.4844222297453324;
float cp_unpack(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 cp_decode(vec4 c){ return vec2(cp_unpack(c.rg), cp_unpack(c.ba)); }
vec2 cp_toMerc(vec2 p){
    float lat = clamp((0.5 - p.y) * CP_PI, -CP_LATMAX, CP_LATMAX);
    float my = log(tan(CP_PI*0.25 + lat*0.5));
    return vec2(p.x, 0.5 - my/(2.0*CP_PI));
}
vec4 cp_hist(int slot, ivec2 tc){
    // GLSL ES 3.00 requires constant indexing into a sampler array -> unrolled fetch.
    for (int i = 0; i < ${TRAIL_LEN}; i++) { if (i == slot) return texelFetch(u_hist[i], tc, 0); }
    return vec4(0.0);
}
void main(){
    int segCount = int(u_trailLen) - 1;
    int pid = gl_VertexID / (6 * segCount);
    int rem = gl_VertexID - pid * (6 * segCount);
    int seg = rem / 6;                 // 0 .. segCount-1  (0 = tail segment)
    int corner = rem - seg * 6;
    float col = mod(float(pid), u_res);
    float row = floor(float(pid) / u_res);
    ivec2 tc = ivec2(int(col), int(row));

    // slot indices: newest = 0, oldest = TRAIL_LEN-1. Tail segment uses the oldest.
    int slotA = (int(u_trailLen) - 1) - seg;          // older end
    int slotB = slotA - 1;                            // newer end (closer to head)
    vec2 pA = cp_decode(cp_hist(slotA, tc));
    vec2 pB = cp_decode(cp_hist(slotB, tc));

    // Discard degenerate / land / antimeridian-wrapping segments (push offscreen).
    vec4 wB = texture(u_vel, pB);
    float dlon = abs(pA.x - pB.x);
    if (wB.a < 0.5 || dlon > 0.5 || (pA.x==0.0&&pA.y==0.0) || (pB.x==0.0&&pB.y==0.0)) {
        v_speed = 0.0; v_t = 0.0; gl_Position = vec4(2.0,2.0,2.0,1.0); return;
    }
    vec2 vel = wB.rg * (2.0*u_vmax) - u_vmax;
    v_speed = length(vel);

    vec4 clipA = projectTile(cp_toMerc(pA));
    vec4 clipB = projectTile(cp_toMerc(pB));
    if (clipA.w <= 0.0 || clipB.w <= 0.0) { v_speed=0.0; v_t=0.0; gl_Position=vec4(2.0,2.0,2.0,1.0); return; }
    vec2 nA = clipA.xy / clipA.w;
    vec2 nB = clipB.xy / clipB.w;
    vec2 dirPx = (nB - nA) * (u_viewport * 0.5);
    vec2 sdir = (length(dirPx) > 1e-4) ? normalize(dirPx) : vec2(0.0, 1.0);
    vec2 perp = vec2(-sdir.y, sdir.x);

    // taper: thickness grows toward the head; segment fraction along the trail
    float fOld = float(seg) / float(segCount);          // 0 tail .. ~1 head
    float fNew = float(seg + 1) / float(segCount);
    // 6 corners of the quad: (-1 end = A/older, +1 end = B/newer); y = +/-1 across width
    vec2 ab[6] = vec2[6](vec2(0.0,-1.0), vec2(1.0,-1.0), vec2(0.0,1.0),
                         vec2(0.0, 1.0), vec2(1.0,-1.0), vec2(1.0,1.0));
    vec2 cc = ab[corner];
    float endF = mix(fOld, fNew, cc.x);                 // along-trail fraction at this vertex
    v_t = endF;
    float thick = u_halfThick * mix(0.25, 1.0, endF);   // taper thin->thick toward head
    vec4 baseClip = (cc.x < 0.5) ? clipA : clipB;
    vec2 baseN    = (cc.x < 0.5) ? nA : nB;
    vec2 offPix = perp * (cc.y * thick);
    vec2 offNDC = offPix * 2.0 / u_viewport;
    gl_Position = baseClip;
    gl_Position.xy += offNDC * baseClip.w;
}`;

const TRAIL_FS = `#version 300 es
precision highp float;
in float v_speed; in float v_t;
out vec4 fragColor;
uniform sampler2D u_cmap;
uniform float u_vmax, u_maxspeed, u_alpha;
void main(){
    float s = clamp(v_speed / u_maxspeed, 0.0, 1.0);
    vec3 c = texture(u_cmap, vec2(s, 0.5)).rgb;
    // fade toward the tail (v_t=0) and slightly boost the head
    float a = u_alpha * smoothstep(0.0, 0.35, v_t) * (0.5 + 0.5*s);
    if (a <= 0.003) discard;
    fragColor = vec4(c, a);
}`;

export function createCurrentParticleGLLayer(map, opts) {
    const {
        sectionKey = 'currents',
        initialConfig = {},
        initialAnimation = {},
        initialCommon = {},
        vmax = 2.5,
        colormap = null,
        maxSpeedColor = (cfg) => vmax,
        landReset = (cfg) => 1.0,
        hourDataUrl = (cfg, hour, bust) => {
            const base = cfg.outfile.replace(/\.png$/, '');
            const f = String(hour).padStart(3, '0');
            return `${window.MAP_UI}/${base}_f${f}_data.png?t=${bust}`;
        },
        refreshMs, syncMs,
    } = opts;

    const A_LYR = `${sectionKey}-trails-layer`;

    let glRef = null, webglFailed = false;
    let updateProg = null, trailProgCache = new Map(), trailProgFailed = false;
    let screenQuad = null, vaoUpdate = null, vaoTrails = null;
    let velTex = null, cmapTex = null;
    // history ring: TRAIL_LEN textures + matching FBOs; head index advances each frame
    let hist = [], histFbo = [], head = 0;
    let RES = 96, count = RES * RES;
    let velReady = false, pendingVelImg = null, pendingLut = null, pendingRebuild = false;
    let curCfg = initialConfig, curAnim = initialAnimation;
    let curSpeed = 0.4, curThick = 2.0, curMaxSpeed = vmax, curAlpha = 0.9, curLandReset = 1.0;
    let bustKey = (timeline.get().refreshEpoch) || Date.now();

    const particleCount = (cfg) => {
        const explicit = parseInt(cfg.particle_count, 10);
        return Math.max(256, explicit > 0 ? explicit : (LOD_COUNT[lodOf(cfg)] || 9000));
    };
    const applyParams = (cfg) => {
        curSpeed = Number(cfg.particle_speed) > 0 ? Number(cfg.particle_speed) : 0.4;
        curThick = Number(cfg.trail_thickness) > 0 ? Number(cfg.trail_thickness) : 2.0;
        curAlpha = Number(cfg.particle_alpha) > 0 ? Number(cfg.particle_alpha) / 100 : 0.9;
        curMaxSpeed = maxSpeedColor(cfg) || vmax;
        curLandReset = landReset(cfg) > 0.5 ? 1.0 : 0.0;
    };

    const compile = (gl, type, src) => {
        const sh = gl.createShader(type); gl.shaderSource(sh, src); gl.compileShader(sh);
        if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
            console.warn(`[${sectionKey} trails] shader:`, gl.getShaderInfoLog(sh)); return null;
        }
        return sh;
    };
    const linkProg = (gl, vsSrc, fsSrc) => {
        const v = compile(gl, gl.VERTEX_SHADER, vsSrc), f = compile(gl, gl.FRAGMENT_SHADER, fsSrc);
        if (!v || !f) return null;
        const p = gl.createProgram(); gl.attachShader(p, v); gl.attachShader(p, f);
        gl.linkProgram(p); gl.deleteShader(v); gl.deleteShader(f);
        if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
            console.warn(`[${sectionKey} trails] link:`, gl.getProgramInfoLog(p)); return null;
        }
        return p;
    };
    // Trail program needs MapLibre's projection prelude (varies by render variant).
    const getTrailProg = (gl, shaderData) => {
        const key = shaderData.variantName || '__default__';
        if (trailProgCache.has(key)) return trailProgCache.get(key);
        if (trailProgFailed) return null;
        const vs = `#version 300 es\n${shaderData.vertexShaderPrelude}\n${shaderData.define}\n${TRAIL_VS_BODY}`;
        const p = linkProg(gl, vs, TRAIL_FS);
        if (!p) { trailProgFailed = true; return null; }
        trailProgCache.set(key, p);
        return p;
    };

    const makeTex = (gl, w, h, data, filter) => {
        const t = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        return t;
    };
    const randomState = () => {
        const d = new Uint8Array(RES * RES * 4);
        for (let i = 0; i < d.length; i += 4) {
            const x = Math.random(), y = Math.random();
            const ex = Math.floor(x * 65535), ey = Math.floor(y * 65535);
            d[i] = (ex >> 8) & 255; d[i+1] = ex & 255; d[i+2] = (ey >> 8) & 255; d[i+3] = ey & 255;
        }
        return d;
    };
    const buildState = (gl) => {
        RES = Math.ceil(Math.sqrt(particleCount(curCfg))); count = RES * RES;
        hist.forEach((t) => t && gl.deleteTexture(t));
        histFbo.forEach((f) => f && gl.deleteFramebuffer(f));
        hist = []; histFbo = []; head = 0;
        const seed = randomState();
        for (let i = 0; i < TRAIL_LEN; i++) {
            const t = makeTex(gl, RES, RES, seed, gl.NEAREST);
            const fbo = gl.createFramebuffer();
            gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
            gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
            hist.push(t); histFbo.push(fbo);
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    };

    // viewBox (equirect bbox) for spawn density — whole world fallback.
    const viewBox = () => {
        try {
            const b = map.getBounds();
            const w = b.getWest(), e = b.getEast(), s = b.getSouth(), n = b.getNorth();
            const toX = (lon) => ((lon + 180) / 360 + 1) % 1;
            const toY = (lat) => 0.5 - lat / 180;
            return [toX(w), toY(n), toX(e), toY(s)];
        } catch { return [0, 0, 1, 1]; }
    };
    let curBbox = [0, 0, 1, 1];

    const uploadVelNow = (gl, img) => {
        if (!velTex) velTex = makeTex(gl, 2, 2, new Uint8Array(16), gl.LINEAR);
        gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        velReady = true;
    };
    const uploadColormapNow = (gl, lut) => {
        if (!lut) return;
        if (!cmapTex) cmapTex = makeTex(gl, 256, 1, lut, gl.LINEAR);
        else { gl.bindTexture(gl.TEXTURE_2D, cmapTex);
               gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut); }
    };
    const loadVelocity = (cfg) => {
        const url = hourDataUrl(cfg, timeline.get().hour, bustKey);
        const img = new Image(); img.crossOrigin = 'anonymous';
        img.onload = () => { pendingVelImg = img; map.triggerRepaint(); };
        img.onerror = () => {};
        img.src = url;
    };

    // Advance the history ring by one: advect newest head -> the slot we overwrite
    // (the oldest), which then becomes the new newest.
    const advect = (gl) => {
        const newestSlot = head;                       // current newest
        const writeSlot = (head + TRAIL_LEN - 1) % TRAIL_LEN; // oldest -> overwrite
        gl.useProgram(updateProg);
        gl.bindVertexArray(vaoUpdate);
        gl.bindFramebuffer(gl.FRAMEBUFFER, histFbo[writeSlot]);
        gl.viewport(0, 0, RES, RES);
        const u = (n) => gl.getUniformLocation(updateProg, n);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, hist[newestSlot]);
        gl.uniform1i(u('u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.uniform1i(u('u_vel'), 1);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform1f(u('u_speed'), curSpeed);
        gl.uniform1f(u('u_dropRate'), 0.002);
        gl.uniform1f(u('u_dropBump'), 0.010);
        gl.uniform1f(u('u_dropSpeed'), 10.0);
        gl.uniform1f(u('u_seed'), Math.random());
        gl.uniform1f(u('u_landReset'), curLandReset);
        gl.uniform4f(u('u_bboxPos'), curBbox[0], curBbox[1], curBbox[2], curBbox[3]);
        gl.disable(gl.BLEND);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        head = writeSlot;                              // new newest = the slot we wrote
    };

    const drawTrails = (gl, args) => {
        const prog = getTrailProg(gl, args.shaderData);
        if (!prog) { webglFailed = true; return; }
        gl.useProgram(prog);
        gl.bindVertexArray(vaoTrails);
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
        // bind the history ring to texture units 0..TRAIL_LEN-1, ordered newest->oldest
        const units = [];
        for (let i = 0; i < TRAIL_LEN; i++) {
            const slot = (head + i) % TRAIL_LEN;       // i=0 newest
            gl.activeTexture(gl.TEXTURE0 + i);
            gl.bindTexture(gl.TEXTURE_2D, hist[slot]);
            units.push(i);
        }
        gl.uniform1iv(u('u_hist'), units);
        gl.activeTexture(gl.TEXTURE0 + TRAIL_LEN); gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.uniform1i(u('u_vel'), TRAIL_LEN);
        gl.activeTexture(gl.TEXTURE0 + TRAIL_LEN + 1); gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(u('u_cmap'), TRAIL_LEN + 1);
        gl.uniform1f(u('u_res'), RES);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform1f(u('u_trailLen'), TRAIL_LEN);
        gl.uniform1f(u('u_halfThick'), Math.max(0.5, curThick));
        gl.uniform1f(u('u_maxspeed'), curMaxSpeed);
        gl.uniform1f(u('u_alpha'), curAlpha);
        gl.uniform2f(u('u_viewport'), gl.drawingBufferWidth, gl.drawingBufferHeight);
        gl.disable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.drawArrays(gl.TRIANGLES, 0, count * 6 * (TRAIL_LEN - 1));
    };

    const makeLayer = (cfg) => ({
        id: A_LYR, type: 'custom', renderingMode: '2d',
        onAdd(m, gl) {
            glRef = gl;
            updateProg = linkProg(gl, QUAD_VS, UPDATE_FS);
            if (!updateProg) { webglFailed = true; return; }
            screenQuad = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, screenQuad);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0,0,1,0,0,1,0,1,1,0,1,1]), gl.STATIC_DRAW);
            vaoUpdate = gl.createVertexArray();
            gl.bindVertexArray(vaoUpdate);
            gl.bindBuffer(gl.ARRAY_BUFFER, screenQuad);
            const loc = gl.getAttribLocation(updateProg, 'a_pos');
            gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
            vaoTrails = gl.createVertexArray();    // attributeless (gl_VertexID)
            gl.bindVertexArray(null);
            cmapTex = makeTex(gl, 1, 1, new Uint8Array([255,255,255,255]), gl.LINEAR);
            buildState(gl);
            applyParams(cfg);
            if (colormap) uploadColormapNow(gl, colormap(cfg));
            loadVelocity(cfg);
            map.triggerRepaint();
        },
        render(gl, args) {
            if (webglFailed || !updateProg) return;
            if (pendingRebuild) { buildState(gl); pendingRebuild = false; }
            if (pendingVelImg) { uploadVelNow(gl, pendingVelImg); pendingVelImg = null; }
            if (pendingLut) { uploadColormapNow(gl, pendingLut); pendingLut = null; }
            if (!velReady || !velTex) { map.triggerRepaint(); return; }
            curBbox = viewBox();
            advect(gl);
            drawTrails(gl, args);
            gl.bindVertexArray(null);
            map.triggerRepaint();
        },
        onRemove(m, gl) {
            trailProgCache.forEach((p) => gl.deleteProgram(p)); trailProgCache.clear();
            hist.forEach((t) => t && gl.deleteTexture(t));
            histFbo.forEach((f) => f && gl.deleteFramebuffer(f));
            [velTex, cmapTex].forEach((t) => t && gl.deleteTexture(t));
            if (updateProg) gl.deleteProgram(updateProg);
            hist = []; histFbo = []; velTex = cmapTex = null; updateProg = null; glRef = null;
        },
    });

    // ---- lifecycle via liveLayerSync (particles always animate; not forecast-gated) ----
    let added = false;
    const mount = (cfg, globals) => {
        curCfg = cfg; curAnim = (globals && globals.animation) || {};
        bustKey = timeline.get().refreshEpoch || Date.now();
        if (!map.getLayer(A_LYR)) { map.addLayer(makeLayer(cfg)); added = true; scrubber.layerActivated && scrubber.layerActivated(); }
    };
    const refresh = (cfg, globals) => {
        curCfg = cfg; curAnim = (globals && globals.animation) || {};
        applyParams(cfg);
        if (colormap) pendingLut = colormap(cfg);
        // reload the velocity texture for the (reconciled) current hour
        bustKey = timeline.get().refreshEpoch || bustKey;
        if (glRef) loadVelocity(cfg);
        if (parseInt(cfg.particle_count, 10) && glRef) pendingRebuild = true;
        map.triggerRepaint();
    };
    const unmount = () => {
        if (added && map.getLayer(A_LYR)) { map.removeLayer(A_LYR); added = false; scrubber.layerDeactivated && scrubber.layerDeactivated(); }
    };

    // Reload velocity texture when the timeline hour changes (scrubber/playback).
    timeline.subscribe((snap) => {
        if (snap.refreshEpoch !== bustKey) bustKey = snap.refreshEpoch;
        if (glRef && velReady) loadVelocity(curCfg);
    });

    liveLayerSync(map, {
        sectionKey, initialConfig,
        initialGlobals: { animation: initialAnimation, common: initialCommon },
        globalKeys: ['animation', 'common'],
        mount, refresh, unmount,
        imageUrl: (cfg) => hourDataUrl(cfg, timeline.get().hour, bustKey),
        refreshMs, syncMs,
    });
}