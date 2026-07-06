import { liveLayerSync } from './_refresh.js';
import { timeline } from './timeline.js';
import { scrubber } from './scrubber.js';
import { flagBackfill } from './_backfill.js';

/**
 * Ocean-current FLOWING STREAMLINE particles as a MapLibre v5 CUSTOM WEBGL LAYER.
 *
 * Each particle is a slowly-drifting HEAD position; its tail is the instantaneous
 * STREAMLINE traced UPSTREAM from the head through the u/v field — i.e. the tail shows
 * where the water just came from. The head is advected on the GPU each frame (one tiny
 * ping-pong state texture); the trail vertex shader integrates backward through u_vel to
 * generate the tail on the fly, projected through MapLibre's projectTile so it follows
 * the globe and stays crisp at any zoom.
 *
 * WHY STREAMLINE (vs the old stored-history ring): the ribbon used to be the last N head
 * positions, so one new vertex was committed per frame. That fused two things that should
 * be independent — on-screen DRIFT SPEED (step x frame-rate) and TAIL LENGTH (step x N).
 * Slowing the drift collapsed the tail to "slow dots". Here the two are decoupled:
 *   drift speed  = head advection step  (u_speed, the particle_speed slider)
 *   tail length  = integration arc      (u_H x STREAM_STEPS, the trail_length slider)
 * so you can have long tails that drift slowly — the "Perpetual Ocean" look. It also only
 * ever samples ONE history texture + u_vel in the vertex stage (vs the old 14+1 sampler
 * binds that capped the tail near the WebGL2 vertex-texture-unit ceiling), and makes the
 * reset-bridge "meteor" class of artifact structurally impossible: a reset just relocates
 * the head and the tail re-integrates from the new spot next frame — no history to bridge.
 *
 * Isolated module — wind and waves (_particles_gl.js, an oriented-quad streak/bar
 * engine) are untouched. This is a deliberate, permanent split, not a stale fork: this
 * file's streamline-ribbon technique is geometrically distinct from an oriented quad and
 * isn't reproducible by _particles_gl.js's primitive modes, so currents keeps its own
 * implementation rather than migrating onto the shared engine.
 *
 * createCurrentParticleGLLayer(map, opts) — opts mirror the wind/waves layer's NAMES
 * where useful (sectionKey, initialConfig, vmax, colormap, hourDataUrl, maxSpeedColor,
 * landReset), for consistency configuring similar-sounding concepts — not because the
 * rendering code is shared. Plus tunables unique to this technique (particle_count,
 * particle_speed=drift, trail_length=tail arc, etc.).
 */

const STREAM_STEPS = 40;        // streamline integration segments (tail = STREAM_STEPS+1 points)
const LOD_COUNT = { 1: 4000, 2: 9000, 3: 18000 };

const lodOf = (cfg) => { const n = parseInt(cfg.level_of_detail, 10); return (n === 1 || n === 3) ? n : 2; };

// 16-bit position packing across two 8-bit channels (no float-texture extension).
const PACK = `
vec2 packPos(float x){ float e = floor(clamp(x,0.0,1.0)*65535.0 + 0.5);
  return vec2(floor(e/256.0)/255.0, mod(e,256.0)/255.0); }
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
vec4 encodePos(vec2 p){ return vec4(packPos(p.x), packPos(p.y)); }
float rand(vec2 co){
    // Dave Hoskins hash (https://www.shadertoy.com/view/4djSRW), copied from
    // _particles_gl.js. The classic fract(sin(dot(co, vec2(12.9898,78.233)))*43758.5)
    // hash has STRONG diagonal correlation (~0.43) — particles respawning with nearby
    // seeds got diagonally-correlated random positions, printing the RNG's structure as
    // dead-straight diagonal lines onto the particle field (the "streaming artifact").
    // This integer-style hash has ~0 correlation, so respawns are genuinely uniform.
    vec3 p3 = fract(vec3(co.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}`;

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
    // Cap the per-frame step magnitude. The 1/coslat term amplifies the longitude step
    // toward the poles (and for fast high-latitude currents), producing single-frame
    // jumps long enough to render as "meteor" streaks that grow with zoom. Clamping the
    // step keeps trails continuous everywhere while removing those over-long segments at
    // the source. MAX_STEP sits above any equatorial/mid-lat step but below the streak
    // threshold, so normal flow is untouched.
    const float MAX_STEP = 0.004;
    float dmag = length(d);
    if (dmag > MAX_STEP) d *= (MAX_STEP / dmag);
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
    // NOTE: do NOT reset particles merely for leaving the view bbox — that confines the
    // whole field to the visible disc and renders as a "petal" cluster on a globe. The
    // bbox is used only to bias where *respawns* land (density where you're looking).
    // Particles flow freely across the globe; they only reset on the drop probability,
    // hitting the poles, or (for ocean layers) wandering onto land.
    bool reset = (rand(seed) < drop) || (npos.y <= 0.0) || (npos.y >= 1.0)
                 || (u_landReset > 0.5 && w.a < 0.5);
    fragColor = encodePos(reset ? randPos : npos);
}`;

// Trail vertex shader BODY (MapLibre projection prelude + #version + define prepended at
// link). One ribbon SEGMENT per (particle, streamline-step). gl_VertexID layout: 6 verts
// per segment, STREAM_STEPS segments per particle. The tail is NOT stored — it is the
// instantaneous streamline integrated UPSTREAM from the head through u_vel, so segment
// `seg` connects streamline point[seg] (head side) to point[seg+1] (tail side). Both
// endpoints are projected via projectTile(toMerc()) into a screen-space quad, tapered +
// faded toward the tail. Only u_head + u_vel are sampled in the vertex stage.
const TRAIL_VS_BODY = `
precision highp float;
uniform sampler2D u_head;     // single head-position state texture (newest positions)
uniform sampler2D u_vel;
uniform float u_res, u_vmax, u_halfThick, u_maxspeed, u_alpha, u_H;
uniform vec2 u_viewport;
out float v_speed;
out float v_t;            // 0 at tail tip .. 1 at head (for fade/taper in FS)
const float CP_PI = 3.141592653589793;
const float CP_LATMAX = 1.4844222297453324;
const float CP_MAXSTEP = 0.005;   // per integration step clamp (tames 1/coslat near poles)
float cp_unpack(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 cp_decode(vec4 c){ return vec2(cp_unpack(c.rg), cp_unpack(c.ba)); }
vec2 cp_toMerc(vec2 p){
    float lat = clamp((0.5 - p.y) * CP_PI, -CP_LATMAX, CP_LATMAX);
    float my = log(tan(CP_PI*0.25 + lat*0.5));
    return vec2(p.x, 0.5 - my/(2.0*CP_PI));
}
// One UPSTREAM (backward) streamline step from p. Uses the SAME field geometry as the
// head advection (vel.x/coslat*0.5, -vel.y) so the tail aligns with how the head moves.
// Tail length therefore scales with current speed (faster water -> longer tail), while
// the head's DRIFT speed is a separate uniform — that is the slow-drift/long-tail decouple.
vec2 cp_step(vec2 p, out bool land){
    vec4 w = texture(u_vel, p);
    land = (w.a < 0.5);
    vec2 vel = land ? vec2(0.0) : (w.rg * (2.0*u_vmax) - u_vmax);
    float lat = (0.5 - p.y) * CP_PI;
    float coslat = max(cos(lat), 0.05);
    vec2 g = vec2(vel.x / coslat * 0.5, -vel.y);
    vec2 disp = -g * u_H;                          // backward = upstream
    float dm = length(disp);
    if (dm > CP_MAXSTEP) disp *= (CP_MAXSTEP / dm);
    vec2 nx = p + disp; nx.x = fract(nx.x + 1.0);
    return nx;
}
void main(){
    int segCount = 20;
    int pid = gl_VertexID / (6 * segCount);
    int rem = gl_VertexID - pid * (6 * segCount);
    int seg = rem / 6;                 // 0 = head segment .. segCount-1 = tail segment
    int corner = rem - seg * 6;
    float col = mod(float(pid), u_res);
    float row = floor(float(pid) / u_res);
    ivec2 tc = ivec2(int(col), int(row));

    vec2 head = cp_decode(texelFetch(u_head, tc, 0));

    // Integrate upstream from the head to this segment's two endpoints:
    //   pB = streamline point[seg]   (head side, newer, brighter, thicker)
    //   pA = streamline point[seg+1] (tail side, older, fainter, thinner)
    vec2 pCur = head, pA = head, pB = head;
    bool ended = false;
    for (int k = 0; k <= segCount; k++){
        if (k == seg)     pB = pCur;
        if (k == seg + 1){ pA = pCur; break; }
        bool hitLand;
        vec2 nx = cp_step(pCur, hitLand);
        if (hitLand) ended = true;
        pCur = ended ? pCur : nx;       // once on land, freeze -> coincident -> discarded
    }

    vec4 wB = texture(u_vel, pB);
    float dlon = abs(pA.x - pB.x);
    float dlat = abs(pA.y - pB.y);
    float seg2 = dlon*dlon + dlat*dlat;
    // discard: land, antimeridian wrap, degenerate (land-frozen coincident), stray-long.
    if (wB.a < 0.5 || dlon > 0.5 || seg2 < 1e-12 || seg2 > (0.02*0.02)) {
        v_speed = 0.0; v_t = 0.0; gl_Position = vec4(2.0,2.0,2.0,1.0); return;
    }
    vec2 vel = wB.rg * (2.0*u_vmax) - u_vmax;
    v_speed = length(vel);

    vec4 clipA = projectTile(cp_toMerc(pA));
    vec4 clipB = projectTile(cp_toMerc(pB));
    // Discard if either endpoint is at/behind the horizon (near-zero w explodes nA/nB).
    if (clipA.w <= 0.0001 || clipB.w <= 0.0001) { v_speed=0.0; v_t=0.0; gl_Position=vec4(2.0,2.0,2.0,1.0); return; }
    vec2 nA = clipA.xy / clipA.w;
    vec2 nB = clipB.xy / clipB.w;
    vec2 dirPx = (nB - nA) * (u_viewport * 0.5);
    vec2 sdir = (length(dirPx) > 1e-4) ? normalize(dirPx) : vec2(0.0, 1.0);
    vec2 perp = vec2(-sdir.y, sdir.x);

    // along-trail fraction: head = 1, tail tip = 0
    float fOld = 1.0 - float(seg + 1) / float(segCount);   // tail side of this segment
    float fNew = 1.0 - float(seg)     / float(segCount);   // head side
    vec2 ab[6] = vec2[6](vec2(0.0,-1.0), vec2(1.0,-1.0), vec2(0.0,1.0),
                         vec2(0.0, 1.0), vec2(1.0,-1.0), vec2(1.0,1.0));
    vec2 cc = ab[corner];
    float endF = mix(fOld, fNew, cc.x);                 // along-trail fraction at this vertex
    v_t = endF;
    float thick = u_halfThick * mix(0.25, 1.0, endF);   // taper thin(tail)->thick(head)
    vec4 baseClip = (cc.x < 0.5) ? clipA : clipB;
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
        backfillKey = null,   // optional resolver (snap)=>{date,run,hour} for backfill
        vmax = 2.5,
        colormap = null,
        maxSpeedColor = (cfg) => vmax,
        landReset = (cfg) => 1.0,
        // Default advection speed when the config doesn't specify particle_speed. This
        // must live in the engine (not injected via initialConfig) because liveLayerSync
        // re-reads the RAW config section on every refresh tick — an initialConfig-only
        // default applies at mount but is lost on the first refresh, dropping curSpeed to
        // the fallback and visibly slowing the flow to "bright dots" after ~20s.
        defaultSpeed = 0.4,
        // How to turn the config into the advection multiplier. Default reads
        // particle_speed as a raw multiplier (back-compat). Currents passes a mapper that
        // translates the 0-100 UI slider into its own min..max speed range.
        speedFromConfig = (cfg) => (Number(cfg.particle_speed) > 0 ? Number(cfg.particle_speed) / 500 : defaultSpeed),
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
    // head position state as a 2-texture ping-pong (advected one step per frame). The tail
    // is the streamline integrated from the head in the trail VS — no stored history ring.
    let headTex = [], headFbo = [], headIdx = 0;
    let RES = 96, count = RES * RES;
    let velReady = false, pendingVelImg = null, pendingLut = null, pendingRebuild = false;
    let curCfg = initialConfig, curAnim = initialAnimation;
    let curSpeed = defaultSpeed, curThick = 2.0, curMaxSpeed = vmax, curAlpha = 0.9, curLandReset = 1.0;
    let curH = 8.0e-4;            // streamline integration step (tail arc); set in applyParams
    let bustKey = (timeline.get().refreshEpoch) || Date.now();

    const particleCount = (cfg) => {
        const explicit = parseInt(cfg.particle_count, 10);
        return Math.max(256, explicit > 0 ? explicit : (LOD_COUNT[lodOf(cfg)] || 9000));
    };
    // Tail length is decoupled from drift speed: it is the per-step integration arc times
    // STREAM_STEPS, independent of u_speed. trail_length is a 0..100 slider mapped into a
    // sensible arc range; faster currents still get proportionally longer tails (the arc
    // scales with local speed inside the shader). Default ~mid.
    const hFromConfig = (cfg) => {
        const t = Number(cfg.trail_length);
        const frac = (t >= 0 && t <= 100) ? t / 100 : 0.5;
        return 2.0e-4 + frac * (1.4e-3 - 2.0e-4);   // ~2e-4 .. 1.4e-3
    };
    const applyParams = (cfg) => {
        curSpeed = speedFromConfig(cfg);
        curThick = Number(cfg.trail_thickness) > 0 ? Number(cfg.trail_thickness) : 2.0;
        curAlpha = Number(cfg.particle_alpha) > 0 ? Number(cfg.particle_alpha) / 100 : 0.9;
        curMaxSpeed = maxSpeedColor(cfg) || vmax;
        curLandReset = landReset(cfg) > 0.5 ? 1.0 : 0.0;
        curH = hFromConfig(cfg);
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
        headTex.forEach((t) => t && gl.deleteTexture(t));
        headFbo.forEach((f) => f && gl.deleteFramebuffer(f));
        headTex = []; headFbo = []; headIdx = 0;
        const seed = randomState();
        for (let i = 0; i < 2; i++) {                  // ping-pong pair
            const t = makeTex(gl, RES, RES, seed, gl.NEAREST);
            const fbo = gl.createFramebuffer();
            gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
            gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
            headTex.push(t); headFbo.push(fbo);
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
    let velRetryTimer = null;
    const loadVelocity = (cfg, bust = bustKey) => {
        const hour = timeline.get().hour;
        const url = hourDataUrl(cfg, hour, bust);
        if (!url) {
            // URL not resolvable yet (currents reconciler not ready) -> retry shortly so
            // the layer appears once forecast_state has loaded, without user action.
            if (!velRetryTimer) velRetryTimer = setTimeout(() => {
                velRetryTimer = null;
                if (timeline.get().hour === hour) loadVelocity(cfg, Date.now());
            }, 2000);
            return;
        }
        const img = new Image(); img.crossOrigin = 'anonymous';
        img.onload = () => { pendingVelImg = img; map.triggerRepaint(); };
        img.onerror = () => {
            // Per-hour velocity texture 404'd — flag demand-driven backfill for this hour,
            // then retry with a FRESH cache-buster after the backfill has had time to land
            // (only if the user is still on this hour). The frozen run-level bustKey alone
            // wouldn't change the URL, so the browser would serve the cached 404.
            flagBackfill(sectionKey, { ...timeline.get(), hour }, backfillKey);
            if (!velRetryTimer) velRetryTimer = setTimeout(() => {
                velRetryTimer = null;
                if (timeline.get().hour === hour) loadVelocity(cfg, Date.now());
            }, 15000);
        };
        img.src = url;
    };

    // Advance the head by one advection step: read the current head, write the other
    // ping-pong slot, then swap. (The tail is not stored — it is re-integrated from the
    // head each frame in the trail VS.)
    const advect = (gl) => {
        const src = headIdx;
        const dst = headIdx ^ 1;
        // Save the GL viewport + FBO MapLibre set up for us; the FBO render below
        // clobbers the viewport (to RES x RES) and binding, and we MUST restore them
        // or the subsequent on-globe draw is confined to a tiny RES x RES box in the
        // bottom-left corner (the "corner square" artifact).
        const prevFbo = gl.getParameter(gl.FRAMEBUFFER_BINDING);
        const prevVp = gl.getParameter(gl.VIEWPORT);
        gl.useProgram(updateProg);
        gl.bindVertexArray(vaoUpdate);
        gl.bindFramebuffer(gl.FRAMEBUFFER, headFbo[dst]);
        gl.viewport(0, 0, RES, RES);
        const u = (n) => gl.getUniformLocation(updateProg, n);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, headTex[src]);
        gl.uniform1i(u('u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.uniform1i(u('u_vel'), 1);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform1f(u('u_speed'), curSpeed);
        // Slow-drift particles travel less ground per lifetime, so keep them alive longer
        // (lower drop) — otherwise slow regions thin out into sparse dots. The streamline
        // tail means a near-stationary head still reads as a flowing streak.
        gl.uniform1f(u('u_dropRate'), 0.0010);
        gl.uniform1f(u('u_dropBump'), 0.0050);
        gl.uniform1f(u('u_dropSpeed'), 10.0);
        gl.uniform1f(u('u_seed'), Math.random());
        gl.uniform1f(u('u_landReset'), curLandReset);
        gl.uniform4f(u('u_bboxPos'), curBbox[0], curBbox[1], curBbox[2], curBbox[3]);
        gl.disable(gl.BLEND);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        // Restore the viewport + FBO so the trail draw covers the full globe.
        gl.bindFramebuffer(gl.FRAMEBUFFER, prevFbo);
        gl.viewport(prevVp[0], prevVp[1], prevVp[2], prevVp[3]);
        headIdx = dst;                                 // swap: dst is the new head
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
        // Only the current head texture + the velocity field are sampled in the vertex
        // stage (2 samplers total — far under the WebGL2 vertex-texture-unit floor).
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, headTex[headIdx]);
        gl.uniform1i(u('u_head'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.uniform1i(u('u_vel'), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(u('u_cmap'), 2);
        gl.uniform1f(u('u_res'), RES);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform1f(u('u_H'), curH);
        gl.uniform1f(u('u_halfThick'), Math.max(0.5, curThick));
        gl.uniform1f(u('u_maxspeed'), curMaxSpeed);
        gl.uniform1f(u('u_alpha'), curAlpha);
        gl.uniform2f(u('u_viewport'), gl.drawingBufferWidth, gl.drawingBufferHeight);
        gl.disable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.drawArrays(gl.TRIANGLES, 0, count * 6 * STREAM_STEPS);
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
            headTex.forEach((t) => t && gl.deleteTexture(t));
            headFbo.forEach((f) => f && gl.deleteFramebuffer(f));
            [velTex, cmapTex].forEach((t) => t && gl.deleteTexture(t));
            if (updateProg) gl.deleteProgram(updateProg);
            headTex = []; headFbo = []; velTex = cmapTex = null; updateProg = null; glRef = null;
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
    // Reload the velocity texture only when the displayed hour (or the data epoch)
    // actually changes — NOT on every timeline tick. While playing, the timeline
    // notifies subscribers every animation frame (frac advancing); reloading the PNG
    // each frame floods the network with fetches and, as they pile up and resolve out
    // of order, the displayed texture drifts out of sync with the particles' motion —
    // the trails decay into slow, near-static bright dots after a play cycle.
    let lastLoadedHour = -1;
    const unsubscribeTimeline = timeline.subscribe((snap) => {
        const epochChanged = snap.refreshEpoch !== bustKey;
        if (epochChanged) bustKey = snap.refreshEpoch;
        if (!glRef || !velReady) return;
        if (snap.hour !== lastLoadedHour || epochChanged) {
            lastLoadedHour = snap.hour;
            loadVelocity(curCfg);
        }
    });

    const stopSync = liveLayerSync(map, {
        sectionKey, initialConfig,
        initialGlobals: { animation: initialAnimation, common: initialCommon },
        globalKeys: ['animation', 'common'],
        mount, refresh, unmount,
        imageUrl: (cfg) => hourDataUrl(cfg, timeline.get().hour, bustKey),
        onMissing: () => flagBackfill(sectionKey, timeline.get(), backfillKey),
        refreshMs, syncMs,
    });

    // Teardown for a basemap style swap (setStyle wipes layers/sources). Stops the sync
    // loop + unmounts the custom layer (its onRemove frees GL programs/textures), and
    // unsubscribes from the timeline so the velocity-reload handler doesn't accumulate.
    return () => {
        try { unsubscribeTimeline(); } catch {}
        try { stopSync(); } catch {}
    };
}