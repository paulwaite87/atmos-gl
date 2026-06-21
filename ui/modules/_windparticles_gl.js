import { liveLayerSync } from './_refresh.js';
import { timeline } from './timeline.js';
import { scrubber } from './scrubber.js';
import { flagBackfill, clearBackfillFlag } from './_backfill.js';

/**
 * Generic u/v-field STREAK PARTICLE engine — a MapLibre v5 CUSTOM WEBGL LAYER
 * (sharp, globe-correct). Originally the wind engine; now shared by any layer with a
 * velocity (u/v) texture: wind, currents, and future flow layers. The consumer picks
 * behaviour via opts (sectionKey, hourDataUrl, colormap, landReset, vmax, ...).
 *
 * Instead of advecting particles into an offscreen canvas that MapLibre stretches over
 * the globe (fuzzy on zoom), it draws each particle DIRECTLY into the map's GL context
 * every frame using MapLibre's `projectTile` projection — so particles are rasterised at
 * screen resolution and follow the globe exactly, staying crisp at any zoom.
 *
 * Each particle is drawn as a short STREAK ALONG the flow every frame — an oriented quad
 * whose length scales with speed and whose opacity fades from a bright leading edge to a
 * faint tail, implying motion. Sharp, zoom-scaling, no smear.
 *
 * Animated path = the custom WebGL layer. Static path = the source PNG raster layer
 * (fallback when animation is off or WebGL is unavailable). Consumed via
 * createWindParticleGLLayer (mount/refresh/unmount driven by liveLayerSync).
 *
 * Consumers: wind.js (via the _windparticles_gl.js re-export shim), currents.js.
 * land masking is opt-in per consumer through landReset (default 0.0 = ignore land).
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
float rand(vec2 co){
    // Dave Hoskins hash (https://www.shadertoy.com/view/4djSRW). The classic
    // fract(sin(dot(co, vec2(12.9898,78.233)))*43758.5) hash has STRONG diagonal
    // correlation (~0.43) — particles respawning with nearby seeds got diagonally-
    // correlated random positions, printing the RNG's structure as dead-straight diagonal
    // lines onto the particle field (the "streaming artifact"). This integer-style hash
    // has ~0 correlation, so respawns are genuinely uniform — no geometric lines.
    vec3 p3 = fract(vec3(co.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}`;

// Alpha-weighted smoothed velocity sample. The raw GFS field is coarse and has sharp
// shear lines / step-changes between cells; particles advecting along it collapse onto
// those discontinuities (the "convergence line" artifacts). This averages the DECODED
// velocity over a Gaussian-ish neighbourhood (radius u_smoothPx texels, ~3 cells) so the
// flow field is coherent and streamlines don't pile onto edges.
//   - ALPHA-WEIGHTED: only taps with data (a>=0.5) contribute, so we never blend the
//     zero of a land/no-data cell into ocean wind (which would CREATE a false gradient
//     and worsen coastline/dateline artifacts).
//   - LONGITUDE WRAP: x offsets wrap via fract(), so the kernel is seamless across the
//     antimeridian (dateline) — no seam there regardless of the encoder's edge columns.
// Returns vec3(vx, vy, coverage): coverage<0.5 means no data nearby (treat as calm).
const WSAMPLE = `
uniform float u_smoothPx;
vec3 sampleWindSmooth(sampler2D tex, vec2 p, float vmax){
    vec2 texel = 1.0 / vec2(textureSize(tex, 0));
    vec2 sumv = vec2(0.0); float sumw = 0.0;
    // 3x3 Gaussian taps scaled by u_smoothPx (radius in texels). u_smoothPx<=0 -> single tap.
    for (int j = -1; j <= 1; j++){
        for (int i = -1; i <= 1; i++){
            vec2 off = vec2(float(i), float(j)) * u_smoothPx * texel;
            vec2 sp = vec2(fract(p.x + off.x + 1.0), clamp(p.y + off.y, 0.0, 1.0));
            vec4 t = texture(tex, sp);
            if (t.a >= 0.5){
                float wgt = exp(-float(i*i + j*j) * 0.6);   // Gaussian-ish falloff
                sumv += (t.rg * (2.0*vmax) - vmax) * wgt;
                sumw += wgt;
            }
        }
    }
    if (sumw <= 0.0) return vec3(0.0, 0.0, 0.0);            // no data in neighbourhood
    return vec3(sumv / sumw, 1.0);
}`;

// Advection in equirectangular [0,1] space. Wind propagates ALONG the encoded velocity
// (no sign flip — that flip is a waves-only convention). Respawns inside the view box,
// which may wrap the antimeridian (bmin.x > bmax.x).
// Advection + AGE LIFECYCLE (windy.com-style). Two render targets via MRT:
//   o_pos: particle position (16-bit packed, as before)
//   o_age: r = normalized age [0,1]; g = per-particle lifetime factor [0.5..1.5]
// Particles advance in age each frame and RESPAWN when age >= 1 (a timed lifecycle, NOT
// the old speed-based "drop"). This is what eliminates clumping: a particle that stalls
// in a low/zero-velocity cell simply ages out and is reborn elsewhere — it can never pile
// up — and the draw pass fades opacity in/out over age for the slow appear/disappear look.
// Particles still respawn if they leave the view bbox or the [0,1] domain.
const UPDATE_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
layout(location = 0) out vec4 o_pos;
layout(location = 1) out vec4 o_age;
uniform sampler2D u_particles;
uniform sampler2D u_age;
uniform sampler2D u_wind;
uniform float u_vmax, u_speed, u_seed, u_landReset, u_ageStep, u_calmSpeed, u_calmDrop;
uniform vec4 u_bboxPos;
const float PI = 3.141592653589793;
const float STEP = 0.0005;
${PACK}
${WSAMPLE}
void main(){
    vec2 pos = decodePos(texture(u_particles, v_uv));
    vec4 ageState = texture(u_age, v_uv);
    float age = ageState.r;
    float lifeFactor = ageState.g > 0.0 ? (0.5 + ageState.g) : 1.0;  // [0.5..1.5]

    float lat = (0.5 - pos.y) * PI;
    vec3 ws = sampleWindSmooth(u_wind, pos, u_vmax);
    vec2 vel = ws.xy;                            // alpha-weighted smoothed velocity
    float hasData = ws.z;                        // 0 = no data nearby
    float coslat = max(cos(lat), 0.05);
    vec2 d = vec2(vel.x / coslat * 0.5, -vel.y) * (u_speed * STEP);
    vec2 npos = pos + d;
    npos.x = fract(npos.x + 1.0);

    // Advance age over this particle's lifetime (longer-lived particles age slower).
    age += u_ageStep / lifeFactor;

    // Respawn position inside the view box (may wrap the antimeridian: bmin.x > bmax.x).
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
    // Calm-cell quick respawn: in genuinely low-wind cells (real troughs / lee zones)
    // particles barely move, so they DWELL and pile up into bright lines even though the
    // data is smooth. Give slow particles a respawn probability that ramps up as speed
    // falls below u_calmSpeed, so they don't accumulate. This is gentle and narrow (only
    // near-calm), unlike the old aggressive speed-drop — the age lifecycle still does the
    // main recycling; this only de-clumps the calm troughs.
    float spd = length(vel);
    float calmDrop = (1.0 - clamp(spd / max(u_calmSpeed, 0.01), 0.0, 1.0)) * u_calmDrop;
    bool calmReset = rand(seed + 3.9) < calmDrop;
    // Respawn on: age expired (timed lifecycle), leaving the domain, calm-cell dwell, or
    // land/no-data when landReset is on.
    bool reset = (age >= 1.0) || (npos.y <= 0.0) || (npos.y >= 1.0)
                 || (u_landReset > 0.5 && hasData < 0.5)
                 || calmReset
                 || outside;

    if (reset) {
        o_pos = encodePos(randPos);
        // New random lifetime factor + age reset to 0 (born fresh, will fade in).
        float nl = rand(seed + 5.1);
        o_age = vec4(0.0, nl, 0.0, 1.0);
    } else {
        o_pos = encodePos(npos);
        o_age = vec4(age, ageState.g, 0.0, 1.0);
    }
}`;

// Draw shaders, parameterized by PRIMITIVE:
//   'streak' (wind/currents): quad long axis ALONG the flow, length scales with speed,
//            opacity fades head->tail (comet look).
//   'bar'    (waves):         quad long axis PERPENDICULAR to the flow (swell crest),
//            fixed length, flat opacity (windy.com swell look).
// Everything up to the orientation is identical (decode, sample, project centre + a
// neighbour ahead, horizon guard, screen-space flow direction).
const buildDrawShaders = (primitive) => {
    const isBar = primitive === 'bar';
    // offPix: which screen axis carries length vs thickness.
    //   streak: length along sdir (flow), thickness along perp.
    //   bar:    length along perp (crest),  thickness along sdir.
    const offPix = isBar
        ? 'vec2 offPix = perp * (cc.x * lenPx) + sdir * (cc.y * u_halfThick);'
        : 'vec2 offPix = sdir * (cc.x * lenPx) + perp * (cc.y * u_halfThick);';
    const VS = `
precision highp float;
uniform sampler2D u_particles;
uniform sampler2D u_age;
uniform sampler2D u_wind;
uniform float u_res, u_vmax, u_halfLen, u_halfThick, u_eps, u_maxspeed, u_lenSpeedScale;
uniform vec2 u_viewport;
out float v_speed;
out float v_along;
out float v_age;
const float WV_PI = 3.141592653589793;
const float WV_LATMAX = 1.4844222297453324;
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
vec2 toMerc(vec2 p){
    float lat = clamp((0.5 - p.y) * WV_PI, -WV_LATMAX, WV_LATMAX);
    float my = log(tan(WV_PI*0.25 + lat*0.5));
    return vec2(p.x, 0.5 - my/(2.0*WV_PI));
}
${WSAMPLE}
void main(){
    int pid = gl_VertexID / 6;
    int corner = gl_VertexID - pid*6;
    float col = mod(float(pid), u_res);
    float row = floor(float(pid) / u_res);
    vec4 s = texelFetch(u_particles, ivec2(int(col), int(row)), 0);
    v_age = texelFetch(u_age, ivec2(int(col), int(row)), 0).r;
    vec2 pos = decodePos(s);
    vec3 ws = sampleWindSmooth(u_wind, pos, u_vmax);
    if (ws.z < 0.5) { v_speed = 0.0; v_along = 0.0; v_age = 0.0; gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
    vec2 vel = ws.xy;                            // alpha-weighted smoothed velocity
    float spd = length(vel);
    v_speed = spd;

    float lat = (0.5 - pos.y) * WV_PI;
    float coslat = max(cos(lat), 0.05);
    // Direction in [0,1] tile space — MUST use the same metric as the advection step in
    // the update shader (vel.x / coslat * 0.5), i.e. the 0.5 equirectangular aspect factor
    // (x spans 360°, y spans 180°, so the x component is compressed 2:1 in [0,1] space).
    // Omitting it doubled the streak's E-W component relative to its actual motion, so
    // particles carrying an east-west component were DRAWN pointing too far east (e.g.
    // SE) even where their real motion had turned more southward — orientation and motion
    // visibly disagreed at flow interfaces.
    vec2 dirEq = vec2(vel.x / coslat * 0.5, -vel.y);
    dirEq = (length(dirEq) > 1e-5) ? normalize(dirEq) : vec2(0.0, -1.0);
    // Forward probe for the streak's screen-space direction. Longitude is periodic, but
    // we must NOT let the probe straddle the antimeridian in screen space: if pos is at
    // lon ~+180 and the step crosses to ~-180, the two projected points land on opposite
    // edges of the map and (aN - cN) becomes a huge horizontal vector (streak explodes),
    // while clamping the probe instead collapses the E-W component (streak flicks vertical
    // — the reported artifact). Fix: probe forward unwrapped; if that lands across the
    // seam, probe BACKWARD instead and negate, so the direction is always a small, correct
    // local step on the same side of the seam.
    vec2 posA = pos + dirEq * u_eps;
    float dirSign = 1.0;
    if (posA.x < 0.0 || posA.x > 1.0) {          // forward step crossed the seam
        posA = pos - dirEq * u_eps;              // probe the other way
        dirSign = -1.0;
        posA.x = clamp(posA.x, 0.0002, 0.9998);  // safe: backward step stays in-domain
    }
    posA.y = clamp(posA.y, 0.0002, 0.9998);

    vec4 cClip = projectTile(toMerc(pos));
    vec4 aClip = projectTile(toMerc(posA));
    // Discard if EITHER endpoint is at/behind the horizon. Guarding only the centre
    // (cClip) leaves a case where the neighbour aClip.w is ~0, making aN explode and the
    // streak span the screen -> massive overdraw -> GPU watchdog hang on rotate. A small
    // positive epsilon keeps us clear of the singular w~0 band at the limb.
    if (cClip.w <= 0.0001 || aClip.w <= 0.0001) { v_speed = 0.0; v_along = 0.0; v_age = 0.0; gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
    vec2 cN = cClip.xy / cClip.w;
    vec2 aN = aClip.xy / aClip.w;
    vec2 pxDir = (aN - cN) * (u_viewport * 0.5) * dirSign;
    vec2 sdir = (length(pxDir) > 1e-4) ? normalize(pxDir) : vec2(0.0, 1.0);   // flow dir (screen)
    vec2 perp = vec2(-sdir.y, sdir.x);

    // length: streak scales with speed (u_lenSpeedScale>0); bar is fixed (scale passed as 0).
    float lenPx = u_halfLen * (1.0 + u_lenSpeedScale * clamp(spd / u_maxspeed, 0.0, 1.0));

    vec2 ab[6] = vec2[6](vec2(-1.0,-1.0), vec2(1.0,-1.0), vec2(-1.0,1.0),
                         vec2(-1.0, 1.0), vec2(1.0,-1.0), vec2( 1.0,1.0));
    vec2 cc = ab[corner];
    v_along = cc.x;                                   // -1 tail .. +1 head
    ${offPix}
    vec2 offNDC = offPix * 2.0 / u_viewport;
    gl_Position = cClip;
    gl_Position.xy += offNDC * cClip.w;
}`;
    // FS: streak fades head->tail; bar is flat alpha.
    // FS: wind streaks fade head->tail (comet) AND fade in/out over the particle's age
    // (the windy.com 'slow appear, slow disappear' look). The age envelope ramps up over
    // the first ~15% of life and down over the last ~25%, so particles are never abruptly
    // born or killed — and a stalled particle fades away before it can visibly clump.
    const FS = `#version 300 es
precision highp float;
in float v_speed;
in float v_along;
in float v_age;
out vec4 fragColor;
uniform sampler2D u_cmap;
uniform float u_maxspeed, u_alpha, u_calmFade;
void main(){
    float t = clamp(v_speed / u_maxspeed, 0.0, 1.0);
    float grad = smoothstep(-1.0, 1.0, v_along);            // bright head, faint tail
    float fadeIn  = smoothstep(0.0, 0.15, v_age);           // born -> visible
    float fadeOut = 1.0 - smoothstep(0.75, 1.0, v_age);     // visible -> gone
    float envelope = fadeIn * fadeOut;
    // Speed fade: dim particles in low-wind areas so real calm troughs read as faint
    // (windy.com style) rather than as bright crowded lines. u_calmFade in [0,1] sets how
    // strongly low speed dims: 0 = no dimming, 1 = calm fully fades to nothing. Ramps with
    // speed up to ~30% of u_maxspeed.
    float spdFade = mix(1.0 - u_calmFade, 1.0, smoothstep(0.0, 0.3, t));
    fragColor = vec4(texture(u_cmap, vec2(t, 0.5)).rgb, u_alpha * grad * envelope * spdFade);
}`;
    return { VS, FS };
};

// ---- controller -----------------------------------------------------------

export function createWindParticleGLController(map, opts) {
    const {
        sectionKey,  // required: 'wind' | 'currents' | ...
        // Visible primitive: 'streak' (along-flow, speed-scaled length, comet fade —
        // wind/currents) or 'bar' (perpendicular crest, fixed length, flat alpha — waves).
        primitive = 'streak',
        initialConfig,
        coordinates = MERCATOR_CORNERS,
        vmax = 40.0,                                      // must match backend VMAX_WIND
        colormap = null,                                  // (cfg) -> Uint8Array(256*4)
        lodCount = null,
        staticUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile}`,
        dataUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile.replace(/\.png$/, '_data.png')}`,
        // When true (wind/currents): drive the velocity field from the shared timeline,
        // reloading per forecast hour. When false (waves): the field is a single static
        // _data.png — skip the timeline subscription entirely and (re)load only on
        // mount/refresh, matching the original standalone wave engine's behaviour.
        useTimeline = true,
        // Optional resolver (snap) => {date,run,hour} for the backfill key, for layers
        // whose run/hour differ from the GFS timeline (currents -> RTOFS). Default null
        // = derive from the GFS run (runEpochUtc) + timeline hour.
        backfillKey = null,
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
        // Advection-step multiplier to compensate for layers whose velocity magnitudes
        // differ from wind's m/s range. The per-frame step is proportional to the raw
        // velocity, so a slow field (ocean currents ~0-2.5 m/s vs wind ~0-40 m/s) barely
        // moves and renders as static specks ("sparklies"). Ocean layers pass a larger
        // speedScale to restore visible flow. Default 1 keeps wind unchanged.
        speedScale = 1.0,
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
        // Age lifecycle (windy.com look): each frame a particle's normalized age advances
        // by ageStep / lifetimeFactor; at age>=1 it respawns. particle_lifetime is frames-
        // to-live (ageStep = 1/N). DEFAULT 70 (was 250): with a long lifetime particles
        // advect across whole oceans before respawning and self-organize into FILAMENTS
        // along the flow's convergence lines (the "streaming artifact" — particles collect
        // onto a track even though the DATA itself is smooth). A shorter life makes them
        // fade and respawn before that bunching becomes visible, like windy.com. Raise
        // toward 120 for longer trails if the filamenting stays acceptable.
        ageStep = (cfg) => { const n = Number(cfg.particle_lifetime);
                             return 1.0 / (isFinite(n) && n > 10 ? n : 70); },
        // Velocity-field smoothing radius in TEXELS for the advection sample. The GFS
        // field is already fairly smooth (the "streaming" lines were particle filamentation
        // from over-long lifetimes, NOT data roughness), so this is a light touch by
        // default — just enough to take the edge off orographic shear without washing out
        // real detail. 0 disables. Config: wind_smooth (cells).
        smoothPx = (cfg) => { const v = Number(cfg.wind_smooth);
                              return isFinite(v) && v >= 0 ? v : 1.5; },
        // Calm-zone handling — stops real low-wind troughs rendering as bright crowded
        // lines. calmSpeed: speed (m/s) below which a cell counts as "calm". calmDrop:
        // peak per-frame respawn probability at zero speed (ramps to 0 at calmSpeed) so
        // slow particles don't dwell/accumulate. calmFade: how strongly low speed dims
        // opacity [0..1] (calm reads faint, like windy.com).
        calmSpeed = (cfg) => { const v = Number(cfg.calm_speed); return isFinite(v) && v > 0 ? v : 2.5; },
        calmDrop  = (cfg) => { const v = Number(cfg.calm_drop);  return isFinite(v) && v >= 0 ? v : 0.06; },
        calmFade  = (cfg) => { const v = Number(cfg.calm_fade);  return isFinite(v) && v >= 0 ? v : 0.6; },
        maxSpeedColor = (cfg) => (Number(cfg.max_speed_color) > 0 ? Number(cfg.max_speed_color) : 30.0),
        useViewDensity = true,
        // Reset particles that wander onto land/no-data (alpha<0.5). Wind blows over
        // land so defaults OFF; ocean layers (currents) set it ON so streaks don't
        // smear across continents.
        landReset = (cfg) => 0.0,
        eps = 0.0015,
        onMount = () => {}, onRefresh = () => {}, onUnmount = () => {},
    } = opts;

    const S_SRC = `${sectionKey}-source`, S_LYR = `${sectionKey}-layer`;
    const A_LYR = `${sectionKey}-anim-layer`;
    // Particles are a visualisation methodology, not forecast stepping: this layer
    // always renders its particles when enabled (gated only by WebGL availability),
    // independent of the global [animation].forecast_stepping switch.
    const isAnimated = () => true;

    let mode = null, webglFailed = false, layerAdded = false;
    let unsubTimeline = null;     // timeline subscription
    let curCfgWind = null;        // latest cfg, for timeline-driven reloads
    let lastWindHour = -1;        // detect hour changes
    let lastWindBust = -1;        // detect data-refresh busts
    let glRef = null;

    let updateProg = null, screenQuad = null, vaoUpdate = null, vaoStreaks = null;
    let windTex = null, cmapTex = null;
    let stateTex = [null, null], ageTex = [null, null], stateFbo = [null, null], stateCur = 0;
    let RES = 256, count = 65536;
    const streakProgCache = new Map();
    let streakProgFailed = false;

    let curSpeed = 0.25, curAlpha = 0.9, curMaxSpeed = 30.0, curStreakLen = 9, curThick = 1.5,
        curAgeStep = 0.014, curSmoothPx = 1.5, curLandReset = 0.0,
        curCalmSpeed = 2.5, curCalmDrop = 0.06, curCalmFade = 0.6;
    let curBbox = [0, 0, 1, 1];
    let windReady = false, pendingWindImg = null, pendingLut = null, pendingRebuild = false;

    const applyParams = (cfg) => {
        curSpeed = speed(cfg) * speedScale; curAlpha = alpha(cfg); curMaxSpeed = maxSpeedColor(cfg);
        curStreakLen = streakLen(cfg); curThick = thickness(cfg);
        curAgeStep = ageStep(cfg); curSmoothPx = smoothPx(cfg);
        curCalmSpeed = calmSpeed(cfg); curCalmDrop = calmDrop(cfg); curCalmFade = calmFade(cfg);
        curLandReset = landReset(cfg) > 0.5 ? 1.0 : 0.0;
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
    // Initial age state: r = random age [0,1] (so particles don't blink in unison),
    // g = random lifetime factor, b/a unused/opaque.
    const randomAge = () => {
        const data = new Uint8Array(RES * RES * 4);
        for (let i = 0; i < RES * RES; i++) {
            data[i*4+0] = Math.floor(Math.random() * 256);   // age
            data[i*4+1] = Math.floor(Math.random() * 256);   // lifetime factor
            data[i*4+2] = 0;
            data[i*4+3] = 255;
        }
        return data;
    };
    const buildState = (gl) => {
        for (let i = 0; i < 2; i++) {
            if (stateTex[i]) gl.deleteTexture(stateTex[i]);
            if (ageTex[i]) gl.deleteTexture(ageTex[i]);
            if (stateFbo[i]) gl.deleteFramebuffer(stateFbo[i]);
            stateTex[i] = makeTex(gl, RES, RES, randomState(), gl.NEAREST);
            ageTex[i]   = makeTex(gl, RES, RES, randomAge(), gl.NEAREST);
            stateFbo[i] = gl.createFramebuffer();
            gl.bindFramebuffer(gl.FRAMEBUFFER, stateFbo[i]);
            // MRT: position -> attachment 0, age -> attachment 1.
            gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, stateTex[i], 0);
            gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, ageTex[i], 0);
            gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
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
        img.onload = () => {
            pendingWindImg = img; map.triggerRepaint();
            // Loaded OK — forget any prior miss for this key so a later eviction re-flags.
            clearBackfillFlag(sectionKey, snap, backfillKey);
        };
        if (useTimeline) {
            img.onerror = () => {
                console.warn(`[${sectionKey}] velocity texture not ready (f${String(snap.hour).padStart(3,'0')}) — flagging backfill`);
                flagBackfill(sectionKey, snap, backfillKey);   // fetch+render this hour
            };
            img.src = hourDataUrl(cfg, snap.hour, snap.refreshEpoch);
        } else {
            // Static field (e.g. waves): single _data.png, cache-busted per (re)load.
            img.onerror = () => console.warn(`[${sectionKey}] velocity texture not ready: ${dataUrl(cfg)}`);
            img.src = `${dataUrl(cfg)}?t=${Date.now()}`;
        }
    };

    const { VS: DRAW_VS_BODY, FS: DRAW_FS } = buildDrawShaders(primitive);
    const getStreakProg = (gl, shaderData) => {
        const key = shaderData.variantName || '__default__';
        if (streakProgCache.has(key)) return streakProgCache.get(key);
        if (streakProgFailed) return null;
        const vs = `#version 300 es\n${shaderData.vertexShaderPrelude}\n${shaderData.define}\n${DRAW_VS_BODY}`;
        const prog = linkProg(gl, vs, DRAW_FS);
        if (!prog) { streakProgFailed = true; return null; }
        streakProgCache.set(key, prog);
        return prog;
    };

    const advect = (gl) => {
        const prevFbo = gl.getParameter(gl.FRAMEBUFFER_BINDING);
        const prevVp = gl.getParameter(gl.VIEWPORT);
        gl.bindVertexArray(vaoUpdate);
        gl.bindFramebuffer(gl.FRAMEBUFFER, stateFbo[1 - stateCur]);
        // MRT: this FBO writes position (attachment 0) + age (attachment 1).
        gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
        gl.viewport(0, 0, RES, RES);
        gl.disable(gl.BLEND);
        gl.useProgram(updateProg);
        const u = (n) => gl.getUniformLocation(updateProg, n);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, stateTex[stateCur]);
        gl.uniform1i(u('u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.uniform1i(u('u_wind'), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, ageTex[stateCur]);
        gl.uniform1i(u('u_age'), 2);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform1f(u('u_speed'), curSpeed);
        gl.uniform1f(u('u_ageStep'), curAgeStep);
        gl.uniform1f(u('u_smoothPx'), curSmoothPx);
        gl.uniform1f(u('u_calmSpeed'), curCalmSpeed);
        gl.uniform1f(u('u_calmDrop'), curCalmDrop);
        gl.uniform1f(u('u_landReset'), curLandReset);
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
        gl.activeTexture(gl.TEXTURE3); gl.bindTexture(gl.TEXTURE_2D, ageTex[stateCur]);
        gl.uniform1i(u('u_age'), 3);
        gl.uniform1f(u('u_res'), RES);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform2f(u('u_viewport'), gl.drawingBufferWidth, gl.drawingBufferHeight);
        gl.uniform1f(u('u_halfLen'), curStreakLen);
        gl.uniform1f(u('u_halfThick'), Math.max(0.5, curThick));
        gl.uniform1f(u('u_eps'), eps);
        gl.uniform1f(u('u_lenSpeedScale'), lenSpeedScale);
        gl.uniform1f(u('u_maxspeed'), curMaxSpeed);
        gl.uniform1f(u('u_smoothPx'), curSmoothPx);
        gl.uniform1f(u('u_calmFade'), curCalmFade);
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
            [windTex, cmapTex, ...stateTex, ...ageTex].forEach((t) => t && gl.deleteTexture(t));
            [...stateFbo].forEach((f) => f && gl.deleteFramebuffer(f));
            if (screenQuad) gl.deleteBuffer(screenQuad);
            if (vaoUpdate) gl.deleteVertexArray(vaoUpdate);
            if (vaoStreaks) gl.deleteVertexArray(vaoStreaks);
            if (updateProg) gl.deleteProgram(updateProg);
            windTex = cmapTex = updateProg = screenQuad = vaoUpdate = vaoStreaks = null;
            stateTex = [null, null]; ageTex = [null, null]; stateFbo = [null, null];
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
        // Static layers (useTimeline:false, e.g. waves) skip this; their single
        // _data.png is loaded by the custom layer's onAdd (and again on refresh).
        if (useTimeline) {
            const snap0 = timeline.get();
            lastWindHour = snap0.hour; lastWindBust = snap0.refreshEpoch;
            unsubTimeline = timeline.subscribe((snap) => {
                if (snap.hour !== lastWindHour || snap.refreshEpoch !== lastWindBust) {
                    lastWindHour = snap.hour; lastWindBust = snap.refreshEpoch;
                    if (curCfgWind) loadWind(curCfgWind);
                }
            });
        }
        if (useTimeline) scrubber.layerActivated();
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
        if (layerAdded && useTimeline) scrubber.layerDeactivated();
        if (map.getLayer(A_LYR)) map.removeLayer(A_LYR);   // triggers onRemove cleanup
        layerAdded = false;
        onUnmount();
    };

    // ---- dispatch ----
    const wanted = (cfg) => (isAnimated() && !webglFailed) ? 'animated' : (staticFallback ? 'static' : 'none');
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
        imageUrl: (cfg) => (isAnimated() && !webglFailed) || !staticFallback
            ? hourDataUrl(cfg, timeline.get().hour, timeline.get().refreshEpoch) : staticUrl(cfg),
    };
}

// Backwards-compatible wrapper (wind): build the controller and drive it from the shared
// liveLayerSync — same shape as the old createParticleWindLayer, so wind.js only swaps
// the import.
export function createWindParticleGLLayer(map, opts) {
    const c = createWindParticleGLController(map, opts);
    // Return the teardown so the host can clean up before a basemap style swap. The
    // controller's unmount (invoked by the teardown) unsubscribes its timeline handler
    // and removes the layer (freeing GL resources in onRemove).
    return liveLayerSync(map, {
        sectionKey: opts.sectionKey,  // required
        initialConfig: opts.initialConfig,
        mount: c.mount, refresh: c.refresh, unmount: c.unmount,
        imageUrl: c.imageUrl,
        // Flag demand-driven backfill when the HEAD probe 404s (separate path from the
        // image onerror), using the same shared deduped flagger + optional resolver.
        onMissing: () => flagBackfill(opts.sectionKey, timeline.get(), opts.backfillKey || null),
        refreshMs: opts.refreshMs, syncMs: opts.syncMs,
    });
}