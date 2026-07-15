import { liveLayerSync } from './_refresh.js';
import { timeline } from './timeline.js';
import { scrubber } from './scrubber.js';
import { flagBackfill } from './_backfill.js';
import { linkProg, makeTex, makeStateTex, randomAge, QUAD_VS, COH_H_FS, COH_V_FS, BLEND_FS } from './_particlegl_primitives.js';

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
 * wind.js also uses this engine now (see its own PROTOTYPE note — it swapped off
 * _particles_gl.js's oriented-quad STREAKS to try this streamline-ribbon technique, not
 * yet committed to permanently). waves.js remains on _particles_gl.js: its BAR primitive
 * marks swell-crest orientation at a point, not a flow trail, so it has no streamline
 * equivalent here — see docs/adr/0003-keep-waves-on-the-oriented-quad-engine.md. This
 * file's streamline-ribbon technique is geometrically distinct from an oriented quad and
 * isn't reproducible by _particles_gl.js's primitive modes, which is why currents (and
 * now wind) use this dedicated implementation instead of that shared one.
 *
 * createCurrentParticleGLLayer(map, opts) — opts mirror the wind/waves layer's NAMES
 * where useful (sectionKey, initialConfig, vmax, colormap, hourDataUrl, maxSpeedColor,
 * landReset), for consistency configuring similar-sounding concepts — not because the
 * rendering code is shared. Plus tunables unique to this technique (particle_speed=drift,
 * trail_length=tail arc, etc.). particle_count is NOT independently configurable -- it's
 * derived from level_of_detail (see LOD_COUNT/lodCount below) so the two settings can't
 * disagree with each other.
 */

const STREAM_STEPS = 40;        // streamline integration segments (tail = STREAM_STEPS+1 points)
const LOD_COUNT = { 1: 4000, 2: 9000, 3: 18000 };

const lodOf = (cfg) => { const n = parseInt(cfg.level_of_detail, 10); return (n === 1 || n === 3) ? n : 2; };

// 16-bit position packing across two 8-bit channels (no float-texture extension).
const PACK = `
#ifdef POS_FLOAT
vec2 decodePos(vec4 c){ return c.xy; }
vec4 encodePos(vec2 p){ return vec4(p, 0.0, 1.0); }
#else
vec2 packPos(float x){ float e = floor(clamp(x,0.0,1.0)*65535.0 + 0.5);
  return vec2(floor(e/256.0)/255.0, mod(e,256.0)/255.0); }
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
vec4 encodePos(vec2 p){ return vec4(packPos(p.x), packPos(p.y)); }
#endif
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

// Velocity sample via BICUBIC B-spline interpolation with MASKED taps -- ported from
// _particles_gl.js's sampleWindSmooth (see tests/gl-shaders/wsample_land_masking.test.js).
// A plain bilinear/bicubic blend pulls the sampled velocity toward zero within ~1 texel
// of any no-data (land) boundary, because encode_uv's NaN encoding (nan_to_num -> 0
// velocity) gets blended in like real data -- measured ~17% velocity underestimation one
// texel from shore. Excluding no-data taps from the weighted sum (and renormalising over
// the valid taps only) fixes it. u_smoothPx=1 (native, near-crisp) unless a caller widens
// it. Returns vec3(vx, vy, coverage).
const VEL_SAMPLE = `
uniform float u_smoothPx;
vec4 cp_bsplineW(float f){
    float f2 = f*f, f3 = f2*f;
    return vec4(
        (1.0 - 3.0*f + 3.0*f2 - f3) / 6.0,
        (4.0 - 6.0*f2 + 3.0*f3)     / 6.0,
        (1.0 + 3.0*f + 3.0*f2 - 3.0*f3) / 6.0,
        f3 / 6.0
    );
}
vec3 sampleVelSmooth(sampler2D tex, vec2 p, float vmax){
    vec2 texSize = vec2(textureSize(tex, 0));
    float s = max(u_smoothPx, 1.0);
    vec2 invReal = 1.0 / texSize;
    vec2 tc = p * (texSize / s) - 0.5;
    vec2 base = floor(tc);
    vec2 f = fract(tc);
    vec4 wx = cp_bsplineW(f.x);
    vec4 wy = cp_bsplineW(f.y);
    // Exact (unfiltered) nearest-texel fetch for the coverage/land test -- see
    // _particles_gl.js's sampleWindSmooth (WSAMPLE) for why texture() here would leak
    // particles marginally onto land near a coastline via the texture's own LINEAR
    // filtering. texelFetch always reads exactly one texel, ignoring the sampler's
    // filter mode.
    vec2 pw = vec2(fract(p.x + 1.0), clamp(p.y, 0.0, 1.0));
    ivec2 texSizeI = ivec2(texSize);
    ivec2 c0px = clamp(ivec2(floor(pw * texSize)), ivec2(0), texSizeI - 1);
    vec4 c0 = texelFetch(tex, c0px, 0);
    vec2 sumv = vec2(0.0);
    float wsum = 0.0;
    for (int j = 0; j < 4; j++){
        for (int i = 0; i < 4; i++){
            vec2 cpos = base + vec2(float(i) - 1.0, float(j) - 1.0);
            vec2 uv = (cpos + 0.5) * s * invReal;
            uv.x = fract(uv.x + 1.0);
            uv.y = clamp(uv.y, 0.0, 1.0);
            vec4 t = texture(tex, uv);
            float w = wx[i] * wy[j];
            float valid = step(0.5, t.a);
            sumv += (t.rg * (2.0*vmax) - vmax) * (w * valid);
            wsum += w * valid;
        }
    }
    if (c0.a < 0.5) return vec3(0.0, 0.0, 0.0);
    if (wsum < 0.01) return vec3(0.0, 0.0, 0.0);
    return vec3(sumv / wsum, 1.0);
}`;

// DIRECTION-COHERENCE filter (opt-in via coherenceRadius; currents never sets it, so this
// stays completely inert there). See _particlegl_primitives.js's COH_H_FS/COH_V_FS
// docstring for how the two-pass separable Gaussian works; runCoherence() below drives them.

// Temporal hour-blending: the forecast field only has one texture per hour, but playback
// advances curFrac continuously between them, so we lerp the current and next hour's
// textures into velBlendTex each frame and sample that single blended texture -- the rest
// of the advection/draw path is unchanged. See _particlegl_primitives.js's BLEND_FS.

// Advection: head position step along u/v (reuses the wind engine's logic verbatim,
// including landReset so trails die on land). MRT: also advances a per-particle age
// (0..1, reset to 0 on respawn) into a second colour attachment -- the trail shader
// fades alpha in/out over it instead of particles popping instantly into/out of view.
// The age texture's g channel holds a per-particle lifetime factor (see UPDATE_FS)
// so particles don't age out in synchronised lockstep either.
const UPDATE_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
layout(location = 0) out vec4 o_pos;
layout(location = 1) out vec4 o_age;
uniform sampler2D u_particles;     // current head positions
uniform sampler2D u_age;
uniform sampler2D u_vel;
uniform float u_vmax, u_speed, u_seed, u_landReset, u_ageStep;
uniform vec4 u_bboxPos;
const float PI = 3.141592653589793;
const float STEP = 0.0005;
${PACK}
${VEL_SAMPLE}
void main(){
    vec2 pos = decodePos(texture(u_particles, v_uv));
    vec4 ageState = texture(u_age, v_uv);
    float age = ageState.r;
    // Per-particle lifetime factor in [0.5..1.5] (g channel, assigned once at spawn --
    // see randomAge()/the reset branch below): longer-lived particles age slower, so the
    // whole field doesn't respawn in visible synchronised waves (ported from
    // _particles_gl.js, which found this the same way -- without it, particles seeded
    // with similar initial ages drift back into lockstep after a few cycles).
    float lifeFactor = ageState.g > 0.0 ? (0.5 + ageState.g) : 1.0;
    vec3 vs0 = sampleVelSmooth(u_vel, pos, u_vmax);
    vec2 vel = vs0.xy;
    float hasData = vs0.z;

    // RK2 (midpoint) integration. A plain Euler step (using only the STARTING velocity)
    // overshoots at sharp direction changes -- the head jumps the full step in the OLD
    // direction, landing somewhere the trail (re-integrated fresh each frame from the
    // NEW head position) already shows flowing a different way, reading as the particle
    // crabbing sideways instead of curving smoothly. Sampling the velocity again at the
    // half-step-ahead MIDPOINT and using THAT for the full step lets the head curve into
    // the changing flow instead (ported from _particles_gl.js, wind's original engine).
    float lat = (0.5 - pos.y) * PI;
    float coslat = max(cos(lat), 0.05);
    vec2 d1 = vec2(vel.x / coslat * 0.5, -vel.y) * (u_speed * STEP);
    vec2 pmid = pos + d1 * 0.5;
    pmid.x = fract(pmid.x + 1.0);
    vec3 vsm = sampleVelSmooth(u_vel, pmid, u_vmax);
    vec2 d;
    if (vsm.z >= 0.5) {                           // midpoint has data -> RK2 step
        float latm = (0.5 - pmid.y) * PI;
        float coslatm = max(cos(latm), 0.05);
        d = vec2(vsm.x / coslatm * 0.5, -vsm.y) * (u_speed * STEP);
    } else {
        d = d1;                                   // midpoint off-data -> fall back to Euler
    }
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
    age += u_ageStep / lifeFactor;
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
    // Particles flow freely across the globe; age expiring is now the ONLY "natural
    // death" trigger, so every regular respawn fades. Poles/land remain instant --
    // position validity, not a natural death, can't keep rendering there -- but those
    // are rare for wind (landReset off, poles seldom reached); for ocean layers with
    // landReset on, a coastal hit is still an unfaded pop AND the trail shader discards
    // a head sitting on no-data outright, so fading it further wouldn't show anyway.
    bool reset = (age >= 1.0) || (npos.y <= 0.0) || (npos.y >= 1.0)
                 || (u_landReset > 0.5 && hasData < 0.5);
    if (reset) {
        o_pos = encodePos(randPos);
        // New random lifetime factor + age reset to 0 (born fresh, will fade in).
        float nl = rand(seed + 5.1);
        o_age = vec4(0.0, nl, 0.0, 1.0);
    } else {
        o_pos = encodePos(npos);
        o_age = vec4(clamp(age, 0.0, 1.0), ageState.g, 0.0, 1.0);
    }
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
uniform sampler2D u_age;      // per-particle age (0..1); drives lifecycle fade in the FS
uniform float u_res, u_vmax, u_halfThick, u_maxspeed, u_alpha, u_H;
uniform vec2 u_viewport;
out float v_speed;
out float v_t;            // 0 at tail tip .. 1 at head (for fade/taper in FS)
out float v_age;          // this particle's lifecycle age (0..1), for fade in/out in FS
const float CP_PI = 3.141592653589793;
const float CP_LATMAX = 1.4844222297453324;
const float CP_MAXSTEP = 0.005;   // per integration step clamp (tames 1/coslat near poles)
${VEL_SAMPLE}
#ifdef POS_FLOAT
vec2 cp_decode(vec4 c){ return c.xy; }
#else
float cp_unpack(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 cp_decode(vec4 c){ return vec2(cp_unpack(c.rg), cp_unpack(c.ba)); }
#endif
vec2 cp_toMerc(vec2 p){
    float lat = clamp((0.5 - p.y) * CP_PI, -CP_LATMAX, CP_LATMAX);
    float my = log(tan(CP_PI*0.25 + lat*0.5));
    return vec2(p.x, 0.5 - my/(2.0*CP_PI));
}
// One UPSTREAM (backward) streamline step from p. Uses the SAME field geometry as the
// head advection (vel.x/coslat*0.5, -vel.y) so the tail aligns with how the head moves.
// Tail length therefore scales with current speed (faster water -> longer tail), while
// the head's DRIFT speed is a separate uniform — that is the slow-drift/long-tail decouple.
// Deliberately kept on the cheap single-tap sample, NOT sampleVelSmooth: this runs inside
// the per-vertex loop below (up to STREAM_STEPS calls x 6 verts/segment x 40 segments), so
// a 16-tap bicubic sample here would multiply an already-hot loop's cost 16x. Wind's
// engine never faced this tradeoff -- it never re-integrates a live loop like this, only
// ever samples once per vertex. The resulting coastline dampening only softens the
// RIBBON'S SHAPE near a coastline; the particle's actual position/movement (UPDATE_FS) and
// the discard/colour sample (wB below) already use the masked sampler.
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
    int segCount = ${STREAM_STEPS};
    int pid = gl_VertexID / (6 * segCount);
    int rem = gl_VertexID - pid * (6 * segCount);
    int seg = rem / 6;                 // 0 = head segment .. segCount-1 = tail segment
    int corner = rem - seg * 6;
    float col = mod(float(pid), u_res);
    float row = floor(float(pid) / u_res);
    ivec2 tc = ivec2(int(col), int(row));

    vec2 head = cp_decode(texelFetch(u_head, tc, 0));
    v_age = texelFetch(u_age, tc, 0).r;

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

    vec3 vsB = sampleVelSmooth(u_vel, pB, u_vmax);
    float dlon = abs(pA.x - pB.x);
    float dlat = abs(pA.y - pB.y);
    float seg2 = dlon*dlon + dlat*dlat;
    // discard: land, antimeridian wrap, degenerate (land-frozen coincident), stray-long.
    if (vsB.z < 0.5 || dlon > 0.5 || seg2 < 1e-12 || seg2 > (0.02*0.02)) {
        v_speed = 0.0; v_t = 0.0; gl_Position = vec4(2.0,2.0,2.0,1.0); return;
    }
    v_speed = length(vsB.xy);

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
// tailFadeEnd: how far along the ribbon (0=tail tip, 1=head) the fade-in reaches full
// opacity. Per-caller (not a module constant) so a consumer with much longer ribbons
// (wind's streamlines vs. currents' own tuned 0.35) can fade more gradually without
// changing every other adopter's look.
const trailFragmentShader = (tailFadeEnd) => `#version 300 es
precision highp float;
in float v_speed; in float v_t; in float v_age;
out vec4 fragColor;
uniform sampler2D u_cmap;
uniform float u_vmax, u_maxspeed, u_alpha;
void main(){
    float s = clamp(v_speed / u_maxspeed, 0.0, 1.0);
    vec3 c = texture(u_cmap, vec2(s, 0.5)).rgb;
    // fade toward the tail (v_t=0) and slightly boost the head
    float aTail = smoothstep(0.0, ${tailFadeEnd.toFixed(3)}, v_t) * (0.5 + 0.5*s);
    // lifecycle fade: ease in over the particle's first 20% of age, ease out over its
    // last 35% -- widened (was 15%/25%) so the transitions themselves read as gradual,
    // not just the total cycle length.
    float fadeIn = smoothstep(0.0, 0.20, v_age);
    float fadeOut = 1.0 - smoothstep(0.65, 1.0, v_age);
    float a = u_alpha * aTail * fadeIn * fadeOut;
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
        // Direction-coherence radius (texels). 0 (the default) disables the filter
        // entirely -- currents never overrides this, so it stays completely inert there.
        coherenceRadius = (cfg) => 0,
        // Masked-bicubic coarse-cell spacing for sampleVelSmooth (see VEL_SAMPLE above).
        // 1 = native (near-crisp); raise only if a sharp shear still tears.
        smoothPx = (cfg) => 1.0,
        // trail_length (0-100) -> integration arc H. Generic fallback range, not tuned
        // for either current consumer -- both wind and currents now set their own
        // hFromConfig (see wind.js/currents.js). A future consumer with no trail_length
        // config field falls through to this range's midpoint.
        hFromConfig = (cfg) => {
            const t = Number(cfg.trail_length);
            const frac = (t >= 0 && t <= 100) ? t / 100 : 0.5;
            return 2.0e-4 + frac * (1.4e-3 - 2.0e-4);   // ~2e-4 .. 1.4e-3
        },
        // Lifecycle length in frames-to-live (ageStep = 1/N): each particle's age advances
        // by ageStep every frame; at age>=1 it respawns. The trail shader fades alpha in/out
        // over fractions of this -- doubled from the first pass (90 -> 180, ~3s at 60fps)
        // because the transitions themselves read as too fast, not just the total cycle.
        ageStep = (cfg) => 1.0 / 180,
        // Trail-length (and, again, advection-speed -- see the note at u_speed in
        // advect()) zoom compensation. comp=1 holds screen-space length/speed
        // ~constant; comp=0 disables (the old, zoom-unaware behaviour). ref is the
        // zoom at which curH/curSpeed apply unscaled. Applying this to speed was tried
        // and reverted three times before (full-strength from zoom 2; half-strength
        // from zoom 5; full-strength from zoom 5), each producing its own visual
        // defect -- since diagnosed as 16-bit position quantization noise dominating
        // an advection step the compensation had shrunk too far, not a flaw in this
        // curve itself. Retrying full-strength from zoom 2 now that floatPos (above)
        // gives positions enough precision to avoid that.
        lengthZoomComp = 1.0,
        lengthZoomRef = 2.0,
        // Zoom-adaptive drawn density (ported from _particles_gl.js): the fixed particle
        // budget concentrates into the shrinking respawn box as you zoom in (viewBox()
        // below), so DRAW fewer of the (randomly distributed) particles to keep on-screen
        // density from silently climbing. densityZoomRef: zoom at/below which the full
        // budget draws. densityZoomFalloff: drawn count halves every 1/falloff zoom levels
        // above ref (0 disables). The update pass still advects the full budget -- only
        // the draw count shrinks, so there's no rebuild and no respawn-rate change.
        densityZoomRef = 3.5,
        densityZoomFalloff = 0.5,
        // Temporal hour-blending (ported from _particles_gl.js): cross-fades the current
        // and next forecast-hour textures by the timeline's playback frac, so playback
        // shows a continuous field instead of a once-per-hour "snap". Always on -- no
        // longer a config-exposed toggle (removed; nobody wanted it off).
        temporalBlend = () => true,
        // trail_thickness (px, ribbon half-thickness) -> curThick. Default reads the
        // config value directly as px; a consumer wanting a different UI scale (e.g. a
        // small integer slider) overrides this to convert, same pattern as
        // hFromConfig/speedFromConfig.
        thicknessFromConfig = (cfg) => Number(cfg.trail_thickness) > 0 ? Number(cfg.trail_thickness) : 2.0,
        hourDataUrl = (cfg, hour, bust) => {
            const base = cfg.outfile.replace(/\.png$/, '');
            const f = String(hour).padStart(3, '0');
            return `${window.MAP_UI}/${base}_f${f}_data.png?t=${bust}`;
        },
        // lodCount is per-consumer (mirrors _particles_gl.js's defaultParticleCount);
        // falls back to this module's currents-tuned LOD_COUNT when not overridden.
        lodCount = null,
        tailFadeEnd = 0.35,   // currents' own tuned default; see trailFragmentShader
        refreshMs, syncMs,
    } = opts;

    const A_LYR = `${sectionKey}-trails-layer`;

    let glRef = null, webglFailed = false;
    let updateProg = null, trailProgCache = new Map(), trailProgFailed = false;
    let screenQuad = null, vaoUpdate = null, vaoTrails = null;
    let velTex = null, cmapTex = null;
    // Direction-coherence (opt-in via coherenceRadius). velCohTex holds the filtered
    // field; cohTempTex is the H-pass intermediate. Rebuilt only when the source texture
    // changes or the radius is retuned (cohDirty), never per frame.
    let cohHProg = null, cohVProg = null, velCohTex = null, velCohNextTex = null, cohTempTex = null, cohFbo = null;
    let cohW = 0, cohH = 0, curCohRadius = 0, cohDirty = false, cohNextDirty = false, velImgW = 0, velImgH = 0;
    // Temporal hour-blending (opt-in via temporalBlend; default on). Mirrors
    // _particles_gl.js's windTexNext/windBlendTex/blendWind/loadWindNext/curFrac.
    let velTexNext = null, velBlendTex = null, velBlendFbo = null, blendProg = null;
    let velNextReady = false, pendingVelNextImg = null;
    let blendW = 0, blendH = 0;
    let curFrac = 0, lastVelNextHour = -1, curTemporalBlend = true;
    let velNextRetryTimer = null;
    // Full-precision particle positions: requires rendering to a float color buffer.
    // With EXT_color_buffer_float, RGBA32F is color-renderable (spec guarantee), so
    // positions store raw [0,1] floats (~1e-7 quantum) instead of the 16-bit packed
    // RGBA8 path (~1.5e-5 quantum) -- at low advection speed (small per-frame step)
    // the packed path's rounding-to-nearest-quantum reads as erratic, jittery
    // "crabbing" instead of smooth motion once the step gets close to that quantum.
    // Ported from _particles_gl.js's floatPos, which hit and fixed this exact issue.
    // Falls back to the 16-bit path where the extension is absent. Detected once in
    // onAdd, before any shader compiles (shaders need to know at compile time).
    let floatPos = false;
    // head position state as a 2-texture ping-pong (advected one step per frame). The tail
    // is the streamline integrated from the head in the trail VS — no stored history ring.
    let headTex = [], headFbo = [], headIdx = 0;
    // Age (0..1, resets to 0 on respawn) ping-ponged alongside the head position via MRT
    // in the advection pass -- drives the fade in/out envelope in the trail shader.
    let ageTex = [];
    let curAgeStep = 1.0 / 90;
    let RES = 96, count = RES * RES;
    let activeCount = count;      // zoom-thinned drawn subset (<= count); see render()
    let velReady = false, pendingVelImg = null, pendingLut = null, pendingRebuild = false;
    let curCfg = initialConfig, curAnim = initialAnimation;
    let curSpeed = defaultSpeed, curThick = 2.0, curMaxSpeed = vmax, curAlpha = 0.9, curLandReset = 1.0;
    let curH = 8.0e-4;            // streamline integration step (tail arc); set in applyParams
    let curSmoothPx = 1.0;        // sampleVelSmooth coarse-cell spacing; set in applyParams
    // Zoom compensation: curH (trail arc) and curSpeed (per-frame advection step) are
    // both fixed UV-space distances, but UV-space maps to ~512*2^zoom screen px --
    // unscaled, both the ribbon's SCREEN length and the particle's SCREEN speed grow
    // every time you zoom in. Scaled by 2^(-lengthZoomComp*(zoom-lengthZoomRef)) each
    // frame to hold both roughly constant on-screen instead, mirroring
    // _particles_gl.js's speedZoomComp -- applied to curH in drawTrails() and to
    // curSpeed in advect(), same factor, since both are just UV distances projected to
    // the same zoom-dependent screen scale.
    let curLengthZoomFactor = 1.0;
    let bustKey = (timeline.get().refreshEpoch) || Date.now();

    // Driven entirely by level_of_detail -- no independent particle_count override, so
    // the two settings can never disagree (see LOD_COUNT/lodCount above).
    const particleCount = (cfg) => {
        return Math.max(256, (lodCount || LOD_COUNT)[lodOf(cfg)] || 9000);
    };
    const applyParams = (cfg) => {
        curSpeed = speedFromConfig(cfg);
        curThick = thicknessFromConfig(cfg);
        curAlpha = Number(cfg.particle_alpha) > 0 ? Number(cfg.particle_alpha) / 100 : 0.9;
        curMaxSpeed = maxSpeedColor(cfg) || vmax;
        curLandReset = landReset(cfg) > 0.5 ? 1.0 : 0.0;
        curH = hFromConfig(cfg);
        const newSmoothPx = Number(smoothPx(cfg));
        curSmoothPx = (isFinite(newSmoothPx) && newSmoothPx >= 1.0) ? newSmoothPx : 1.0;
        const newCoh = Number(coherenceRadius(cfg)) || 0;
        if (newCoh !== curCohRadius) { curCohRadius = newCoh; cohDirty = true; }
        const newAgeStep = Number(ageStep(cfg));
        curAgeStep = (isFinite(newAgeStep) && newAgeStep > 0) ? newAgeStep : (1.0 / 90);
        curTemporalBlend = temporalBlend(cfg);
    };

    // compile/linkProg/makeTex/makeStateTex/randomAge are shared with _particles_gl.js via
    // _particlegl_primitives.js; randomState stays here -- see that module's docstring for
    // why. Trail program needs MapLibre's projection prelude (varies by render variant).
    const getTrailProg = (gl, shaderData) => {
        const key = shaderData.variantName || '__default__';
        if (trailProgCache.has(key)) return trailProgCache.get(key);
        if (trailProgFailed) return null;
        const vs = `#version 300 es\n${shaderData.vertexShaderPrelude}\n${shaderData.define}\n${TRAIL_VS_BODY}`;
        const p = linkProg(gl, vs, trailFragmentShader(tailFadeEnd), floatPos, sectionKey);
        if (!p) { trailProgFailed = true; return null; }
        trailProgCache.set(key, p);
        return p;
    };

    const randomState = () => {
        if (floatPos) {                                  // raw [0,1] positions, full precision
            const d = new Float32Array(RES * RES * 4);
            for (let i = 0; i < RES * RES; i++) {
                d[i*4] = Math.random(); d[i*4+1] = Math.random(); d[i*4+2] = 0.0; d[i*4+3] = 1.0;
            }
            return d;
        }
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
        activeCount = count;   // render() recomputes the zoom-thinned value before the next draw
        headTex.forEach((t) => t && gl.deleteTexture(t));
        headFbo.forEach((f) => f && gl.deleteFramebuffer(f));
        ageTex.forEach((t) => t && gl.deleteTexture(t));
        headTex = []; headFbo = []; ageTex = []; headIdx = 0;
        const seed = randomState();
        for (let i = 0; i < 2; i++) {                  // ping-pong pair
            const t = makeStateTex(gl, RES, RES, seed, floatPos);
            const a = makeTex(gl, RES, RES, randomAge(RES), gl.NEAREST);
            const fbo = gl.createFramebuffer();
            gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
            gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
            gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, a, 0);
            headTex.push(t); ageTex.push(a); headFbo.push(fbo);
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    };

    // viewBox (equirect bbox) for spawn density -- ported from _particles_gl.js's more
    // robust version (whole world fallback). The naive map.getBounds()-only version this
    // replaced had three latent bugs wind's engine already found and fixed:
    //   1. No latitude padding -- respawns land exactly at the visible edge, so new
    //      particles visibly pop in right at the screen boundary instead of just outside it.
    //   2. No minimum-height floor -- getBounds()'s N/S span can collapse toward zero at
    //      high zoom, which is what made wind's particles vanish above ~zoom 7 before this
    //      fix landed there.
    //   3. Longitude taken directly from getBounds()'s east/west -- on a GLOBE projection
    //      (not flat mercator), geographic bounds don't map cleanly to what's actually
    //      visible on a rotated/tilted globe. Wind's fix instead derives the longitude span
    //      from the ACTUAL VIEWPORT PIXEL WIDTH (worldPx = 512*2^zoom), which is
    //      geometrically correct regardless of globe rotation.
    // currents' viewBox() is exposed to the identical class of bug -- same globe
    // projection, same getBounds()-based respawn biasing.
    const viewBox = () => {
        try {
            const b = map.getBounds();
            let n = b.getNorth(), s = b.getSouth();
            if (!Number.isFinite(n) || !Number.isFinite(s)) return [0, 0, 1, 1];
            const padLat = Math.max(0, n - s) * 0.15;
            n = Math.min(89.9, n + padLat); s = Math.max(-89.9, s - padLat);
            let yN = Math.max(0, (90 - n) / 180), yS = Math.min(1, (90 - s) / 180);
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
            spanLon = Math.max(1.0, spanLon);
            const cl = ((((c.lng + 180) % 360) + 360) % 360) / 360;
            const half = (spanLon / 360) / 2;
            const lonMin = ((((cl - half) % 1) + 1) % 1);
            const lonMax = ((((cl + half) % 1) + 1) % 1);
            return [lonMin, yN, lonMax, yS];
        } catch (_) { return [0, 0, 1, 1]; }
    };
    let curBbox = [0, 0, 1, 1];

    const uploadVelNow = (gl, img) => {
        if (!velTex) velTex = makeTex(gl, 2, 2, new Uint8Array(16), gl.LINEAR);
        gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        velReady = true;
        velImgW = img.naturalWidth || img.width || 0;
        velImgH = img.naturalHeight || img.height || 0;
        cohDirty = true;   // new raw texture -> the coherent copy is stale
    };
    const uploadVelNextNow = (gl, img) => {
        if (velTexNext) gl.deleteTexture(velTexNext);
        velTexNext = makeTex(gl, 2, 2, new Uint8Array(16), gl.LINEAR);
        gl.bindTexture(gl.TEXTURE_2D, velTexNext);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        velNextReady = true;
        cohNextDirty = true;
    };

    // ---- direction-coherence (opt-in; see COH_H_FS/COH_V_FS above) ----
    const cohActive = () => curCohRadius > 0.001 && cohHProg && cohVProg;
    // Set once per frame in render() (raw, coherent, or blended -- whichever applies);
    // advect()/drawTrails() both just read this rather than recomputing it twice.
    let activeVelTexRef = null;
    const activeVelTex = () => activeVelTexRef || velTex;
    const makeCohTarget = (gl, w, h) => {
        const t = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        return t;
    };
    const ensureCohTextures = (gl, w, h) => {
        if (velCohTex && cohW === w && cohH === h) return;
        [velCohTex, velCohNextTex, cohTempTex].forEach((t) => t && gl.deleteTexture(t));
        velCohTex = makeCohTarget(gl, w, h);
        velCohNextTex = makeCohTarget(gl, w, h);
        cohTempTex = makeCohTarget(gl, w, h);
        if (!cohFbo) cohFbo = gl.createFramebuffer();
        cohW = w; cohH = h;
    };
    // Lazily (re)create the blend output texture + FBO to match the velocity texture size.
    const ensureBlendTex = (gl, w, h) => {
        if (velBlendTex && blendW === w && blendH === h) return;
        if (velBlendTex) gl.deleteTexture(velBlendTex);
        velBlendTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, velBlendTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        if (!velBlendFbo) velBlendFbo = gl.createFramebuffer();
        blendW = w; blendH = h;
    };
    // Build the direction-coherent copy of srcTex into dstTex via two separable passes.
    // Runs only when the source changes or the radius is retuned (cohDirty/cohNextDirty),
    // not per frame. Generalised to explicit (src,dst) params (rather than a single
    // hardcoded velTex->velCohTex) so it can serve BOTH the current-hour and next-hour
    // textures independently -- coherence-then-blend needs both filtered before mixing.
    const runCoherence = (gl, srcTex, dstTex) => {
        if (!srcTex || !cohHProg || !cohVProg || velImgW <= 0 || velImgH <= 0) return;
        ensureCohTextures(gl, velImgW, velImgH);
        const prevFbo = gl.getParameter(gl.FRAMEBUFFER_BINDING);
        const prevVp = gl.getParameter(gl.VIEWPORT);
        const tx = 1.0 / velImgW, ty = 1.0 / velImgH;
        gl.bindFramebuffer(gl.FRAMEBUFFER, cohFbo);
        gl.viewport(0, 0, velImgW, velImgH);
        gl.disable(gl.BLEND);
        gl.bindVertexArray(vaoUpdate);
        // Pass H: srcTex -> cohTempTex
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, cohTempTex, 0);
        gl.useProgram(cohHProg);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, srcTex);
        gl.uniform1i(gl.getUniformLocation(cohHProg, 'u_src'), 0);
        gl.uniform1f(gl.getUniformLocation(cohHProg, 'u_vmax'), vmax);
        gl.uniform1f(gl.getUniformLocation(cohHProg, 'u_radius'), curCohRadius);
        gl.uniform2f(gl.getUniformLocation(cohHProg, 'u_texel'), tx, ty);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        // Pass V: cohTempTex -> dstTex
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, dstTex, 0);
        gl.useProgram(cohVProg);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, cohTempTex);
        gl.uniform1i(gl.getUniformLocation(cohVProg, 'u_src'), 0);
        gl.uniform1f(gl.getUniformLocation(cohVProg, 'u_vmax'), vmax);
        gl.uniform1f(gl.getUniformLocation(cohVProg, 'u_radius'), curCohRadius);
        gl.uniform2f(gl.getUniformLocation(cohVProg, 'u_texel'), tx, ty);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        gl.bindFramebuffer(gl.FRAMEBUFFER, prevFbo);
        gl.viewport(prevVp[0], prevVp[1], prevVp[2], prevVp[3]);
    };
    // One blend pass: velBlendTex = mix(srcA, srcB, curFrac). Saves/restores FBO.
    const blendVel = (gl, srcA, srcB) => {
        ensureBlendTex(gl, velImgW, velImgH);
        const prevFbo = gl.getParameter(gl.FRAMEBUFFER_BINDING);
        const prevVp = gl.getParameter(gl.VIEWPORT);
        gl.bindFramebuffer(gl.FRAMEBUFFER, velBlendFbo);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, velBlendTex, 0);
        gl.viewport(0, 0, blendW, blendH);
        gl.disable(gl.BLEND);
        gl.useProgram(blendProg);
        gl.bindVertexArray(vaoUpdate);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, srcA);
        gl.uniform1i(gl.getUniformLocation(blendProg, 'u_a'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, srcB);
        gl.uniform1i(gl.getUniformLocation(blendProg, 'u_b'), 1);
        gl.uniform1f(gl.getUniformLocation(blendProg, 'u_blend'), curFrac);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        gl.bindFramebuffer(gl.FRAMEBUFFER, prevFbo);
        gl.viewport(prevVp[0], prevVp[1], prevVp[2], prevVp[3]);
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

    // Preload the NEXT forecast hour for temporal blending. Clamped at maxHour (no hour
    // beyond the end; the timeline loops to minHour there, so we just hold the last hour
    // and let curFrac be a no-op until the wrap). A miss here is quiet -- loadVelocity
    // already flags backfill for the CURRENT hour; the blend simply stays off (falls back
    // to the raw current-hour texture) until the next hour's texture arrives on its own.
    const loadVelocityNext = (cfg, snap) => {
        if (!curTemporalBlend) return;
        const nextHour = snap.hour < snap.maxHour ? snap.hour + 1 : snap.hour;
        if (nextHour === lastVelNextHour && velNextReady) return;
        const url = hourDataUrl(cfg, nextHour, bustKey);
        if (!url) {
            if (!velNextRetryTimer) velNextRetryTimer = setTimeout(() => {
                velNextRetryTimer = null;
                loadVelocityNext(cfg, timeline.get());
            }, 2000);
            return;
        }
        lastVelNextHour = nextHour;
        velNextReady = false;
        const img = new Image(); img.crossOrigin = 'anonymous';
        img.onload = () => { pendingVelNextImg = img; map.triggerRepaint(); };
        img.onerror = () => { /* next-hour miss: blend simply stays off until it arrives */ };
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
        gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
        gl.viewport(0, 0, RES, RES);
        const u = (n) => gl.getUniformLocation(updateProg, n);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, headTex[src]);
        gl.uniform1i(u('u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, activeVelTex());
        gl.uniform1i(u('u_vel'), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, ageTex[src]);
        gl.uniform1i(u('u_age'), 2);
        gl.uniform1f(u('u_vmax'), vmax);
        // Scaled by curLengthZoomFactor (same curve as the trail ribbon, full strength
        // from lengthZoomRef) -- fourth attempt at zoom-compensated speed. The first
        // three (see lengthZoomComp above) produced stationary-looking particles,
        // fast particles piling up at boundaries, and crabbing; all three are now
        // understood to be symptoms of 16-bit position quantization noise dominating
        // an advection step shrunk small enough by the compensation -- exactly the bug
        // floatPos above fixes. Retrying now that positions are full-precision.
        gl.uniform1f(u('u_speed'), curSpeed * curLengthZoomFactor);
        gl.uniform1f(u('u_smoothPx'), curSmoothPx);
        gl.uniform1f(u('u_ageStep'), curAgeStep);
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
        // Head texture + velocity field + age are sampled in the vertex stage (3 samplers
        // total — still far under the WebGL2 vertex-texture-unit floor).
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, headTex[headIdx]);
        gl.uniform1i(u('u_head'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, activeVelTex());
        gl.uniform1i(u('u_vel'), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(u('u_cmap'), 2);
        gl.activeTexture(gl.TEXTURE3); gl.bindTexture(gl.TEXTURE_2D, ageTex[headIdx]);
        gl.uniform1i(u('u_age'), 3);
        gl.uniform1f(u('u_res'), RES);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform1f(u('u_smoothPx'), curSmoothPx);
        gl.uniform1f(u('u_H'), curH * curLengthZoomFactor);
        gl.uniform1f(u('u_halfThick'), Math.max(0.5, curThick));
        gl.uniform1f(u('u_maxspeed'), curMaxSpeed);
        gl.uniform1f(u('u_alpha'), curAlpha);
        gl.uniform2f(u('u_viewport'), gl.drawingBufferWidth, gl.drawingBufferHeight);
        gl.disable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        // Drawing the first activeCount particles is a uniform spatial sample: pid (grid
        // index) has no correlation with a particle's actual (randomly assigned) position,
        // so thinning by draw-count alone (not skipping/rebuilding) stays evenly
        // distributed. The update pass above still advects the FULL budget regardless.
        gl.drawArrays(gl.TRIANGLES, 0, activeCount * 6 * STREAM_STEPS);
    };

    const makeLayer = (cfg) => ({
        id: A_LYR, type: 'custom', renderingMode: '2d',
        onAdd(m, gl) {
            glRef = gl;
            // Must be detected before any shader compiles -- linkProg's #define
            // injection reads this flag.
            floatPos = !!gl.getExtension('EXT_color_buffer_float');
            updateProg = linkProg(gl, QUAD_VS, UPDATE_FS, floatPos, sectionKey);
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
            cohHProg = linkProg(gl, QUAD_VS, COH_H_FS, floatPos, sectionKey);
            cohVProg = linkProg(gl, QUAD_VS, COH_V_FS, floatPos, sectionKey);
            blendProg = linkProg(gl, QUAD_VS, BLEND_FS, floatPos, sectionKey);
            buildState(gl);
            applyParams(cfg);
            if (colormap) uploadColormapNow(gl, colormap(cfg));
            loadVelocity(cfg);
            loadVelocityNext(cfg, timeline.get());   // preload next hour for blending
            map.triggerRepaint();
        },
        render(gl, args) {
            if (webglFailed || !updateProg) return;
            if (pendingRebuild) { buildState(gl); pendingRebuild = false; }
            if (pendingVelImg) { uploadVelNow(gl, pendingVelImg); pendingVelImg = null; }
            if (pendingVelNextImg) { uploadVelNextNow(gl, pendingVelNextImg); pendingVelNextImg = null; }
            if (pendingLut) { uploadColormapNow(gl, pendingLut); pendingLut = null; }
            if (!velReady || !velTex) { map.triggerRepaint(); return; }
            curBbox = viewBox();
            let zNow = lengthZoomRef;
            try { zNow = map.getZoom(); } catch (_) { /* keep ref */ }
            if (lengthZoomComp > 0) {
                const raw = Math.pow(2, -lengthZoomComp * (zNow - lengthZoomRef));
                // Floor was 0.05, which stops compensating (and lets the ribbon's screen
                // length start growing again) above ~zoom 6.3 -- easily reached by
                // normal navigation. Dropped to 1e-6 so compensation keeps working up to
                // ~zoom 22 (MapLibre's default ceiling); a nonzero floor is kept only as
                // a guard against a literal zero-length/NaN ribbon.
                curLengthZoomFactor = Math.min(4.0, Math.max(1e-6, raw));
            } else {
                curLengthZoomFactor = 1.0;
            }
            if (densityZoomFalloff > 0) {
                const f = Math.pow(2, -Math.max(0, zNow - densityZoomRef) * densityZoomFalloff);
                const floorN = Math.max(256, Math.round(count * 0.06));
                activeCount = Math.min(count, Math.max(floorN, Math.round(count * f)));
            } else {
                activeCount = count;
            }
            // Temporal interpolation: when playing (curFrac>0) and the next hour is loaded,
            // lerp current->next into velBlendTex and advect/draw against that. Paused or
            // next-not-ready -> sample the current hour directly (the old hard-cut path).
            // Live direction-coherence: rebuild the coherent texture(s) only when a source
            // changed or the radius was retuned, then blend/sample those instead of the raw.
            let sCur = velTex, sNext = velTexNext;
            if (cohActive()) {
                if (cohDirty && velTex) { runCoherence(gl, velTex, velCohTex); cohDirty = false; }
                if (cohNextDirty && velTexNext) { runCoherence(gl, velTexNext, velCohNextTex); cohNextDirty = false; }
                if (velCohTex) sCur = velCohTex;
                if (velCohNextTex && velNextReady) sNext = velCohNextTex;
            }
            if (curTemporalBlend && blendProg && velNextReady && sNext
                && curFrac > 0.001 && velImgW > 0) {
                blendVel(gl, sCur, sNext);
                activeVelTexRef = velBlendTex;
            } else {
                activeVelTexRef = sCur;
            }
            advect(gl);
            drawTrails(gl, args);
            gl.bindVertexArray(null);
            map.triggerRepaint();
        },
        onRemove(m, gl) {
            trailProgCache.forEach((p) => gl.deleteProgram(p)); trailProgCache.clear();
            headTex.forEach((t) => t && gl.deleteTexture(t));
            ageTex.forEach((t) => t && gl.deleteTexture(t));
            headFbo.forEach((f) => f && gl.deleteFramebuffer(f));
            [velTex, cmapTex, velCohTex, velCohNextTex, cohTempTex,
                velTexNext, velBlendTex].forEach((t) => t && gl.deleteTexture(t));
            if (cohFbo) gl.deleteFramebuffer(cohFbo);
            if (velBlendFbo) gl.deleteFramebuffer(velBlendFbo);
            [updateProg, cohHProg, cohVProg, blendProg].forEach((p) => p && gl.deleteProgram(p));
            headTex = []; headFbo = []; ageTex = []; velTex = cmapTex = null;
            velCohTex = velCohNextTex = cohTempTex = cohFbo = null; cohW = cohH = 0;
            updateProg = cohHProg = cohVProg = blendProg = null;
            velTexNext = velBlendTex = velBlendFbo = activeVelTexRef = null;
            velNextReady = false; pendingVelNextImg = null;
            blendW = blendH = 0; curFrac = 0; lastVelNextHour = -1;
            if (velNextRetryTimer) { clearTimeout(velNextRetryTimer); velNextRetryTimer = null; }
            glRef = null;
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
        if (glRef) { loadVelocity(cfg); loadVelocityNext(cfg, timeline.get()); }
        if (parseInt(cfg.level_of_detail, 10) && glRef) pendingRebuild = true;
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
        curFrac = snap.frac || 0;   // every frame while playing; 0 when paused
        const epochChanged = snap.refreshEpoch !== bustKey;
        if (epochChanged) bustKey = snap.refreshEpoch;
        if (!glRef || !velReady) return;
        if (snap.hour !== lastLoadedHour || epochChanged) {
            lastLoadedHour = snap.hour;
            loadVelocity(curCfg);
            loadVelocityNext(curCfg, snap);
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