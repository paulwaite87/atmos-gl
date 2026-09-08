import { liveLayerSync } from './_refresh.js';
import { timeline } from './timeline.js';
import { scrubber } from './scrubber.js';
import { flagBackfill } from './_backfill.js';
import { linkProg } from './_particlegl_primitives.js';

/**
 * GPU scalar-field FILL as a MapLibre v5 CUSTOM WEBGL LAYER.
 *
 * Why this exists: the older createAnimatedRasterLayer renders the field into a
 * fixed-size offscreen canvas, which MapLibre then stretches onto the globe — so
 * zooming in magnifies that baked raster and the band edges show canvas-pixel
 * stair-steps. This module instead draws a lon/lat mesh directly into MapLibre's
 * own GL context every frame, projecting each vertex through MapLibre's projection
 * (projectTile/toMerc, identical to the wind/wave particle layers). The fragment
 * shader samples the data texture per SCREEN pixel and runs the layer's shade(),
 * so band edges are crisp at any zoom with no intermediate raster — and no need
 * for a heavy level_of_detail canvas.
 *
 * Mode dispatch: each mount/refresh picks 'fill' (this GPU path, hour-animated) or
 * 'static' (a single always-fresh raster image, see createStaticFillLayer's shape)
 * via `forecastStepping(curAnim)`. 'static' is also the PERMANENT fallback once the
 * custom-layer shader fails to build once (`webglFailed` latches for the rest of
 * this page load — a later forecastStepping()===true does not revert it back to
 * 'fill').
 *
 * Options (20 call sites today — see docs/conventions/temperature.md for a worked
 * example):
 *
 * Required —
 *
 * `sectionKey` (string) — the join-key for this layer's MapLibre source/layer ids
 * AND the config section this reads from `/api/config` — the same bare string
 * threaded through `ALL_LAYERS`/`TASK_CLASSES`/`config/atmos-gl.json`, unenforced
 * (see docs/conventions/layers.md's join-key fragility note).
 *
 * `initialConfig` (object) — this layer's own section's config values, used for the
 * very first mount before the reconcile loop's own live `/api/config` poll takes over.
 *
 * `vmin`/`vspan` (numbers) — the physical value range a decoded texture sample maps
 * onto: `value = decodedSample * vspan + vmin`, decodedSample already in [0, 1].
 *
 * `fragmentBody` (GLSL source string) — defines `vec4 shade(float value, vec2 uv)`,
 * the per-pixel colorizer. `value` is already decoded to physical units (see
 * `vmin`/`vspan` above); `uv` is the equirect [0, 1] sample coordinate (x=lon,
 * y=lat, north→south) — a caller needing a second sample (e.g. for a derived
 * quantity) can re-sample `u_tex0`/`u_tex1` at a different `uv` itself.
 *
 * Value decoding & sampling —
 *
 * `valueDecode` (GLSL expression string referencing `d`, a `vec4` texel; default
 * `(d.r * 65280.0 + d.g * 255.0) / 65535.0`, the standard two-channel 16-bit LUT
 * decode `lib/texture.py`'s `encode_frames` produces) — override only for a texture
 * encoded some other way.
 *
 * `bicubic` (bool, default `false`) — bicubic (4×4 tap) vs. the texture's own native
 * filtering for `sampleVal`. Smoother gradients at high zoom, at a real per-pixel
 * cost; every current `SPECS`-backed scalar field layer sets this `true`.
 *
 * Uniforms & colour —
 *
 * `customUniforms` (fn(cfg) => object, default `() => ({})`) — extra uniform
 * name→value pairs uploaded every render. A 2/3/4-length array uploads via
 * `uniform{2,3,4}fv`; anything else uploads via `uniform1f`. A name with no matching
 * `uniform` declared in `fragmentBody` is silently skipped (`getUniformLocation`
 * returns null) — a typo here fails silent, not loud.
 *
 * `colormap` (fn(cfg) => Uint8ClampedArray|null, read from `opts.colormap` directly
 * rather than destructured, default `null`) — a 256×1 RGBA LUT uploaded as `u_cmap`
 * on mount and on every refresh; returning a falsy value leaves whatever LUT was
 * previously uploaded bound (skip re-upload rather than clear).
 *
 * Static-fallback-only —
 *
 * `opacity` (number 0–1, default `0.9`) — ONLY the static-fallback raster layer's
 * `raster-opacity` paint property. The GPU fill path itself never reads this — its
 * alpha comes from `shade()`'s returned `vec4` (typically via a `customUniforms`
 * alpha uniform), so setting `opacity` alone has no visible effect while 'fill' mode
 * is active.
 *
 * Global config wiring —
 *
 * `initialAnimation`/`initialCommon` (objects, default `{}`) — initial values for the
 * "animation"/"common" global config sections (see `liveLayerSync`'s `globalKeys`),
 * used before the reconcile loop's own poll supplies live ones. `forecastStepping`
 * (below) reads `animation.forecast_stepping`; `initialCommon` is tracked in
 * parallel for the same wiring but isn't read anywhere in this function today.
 *
 * `forecastStepping` (fn(anim) => bool, default
 * `(anim) => anim && anim.forecast_stepping !== false`) — decides 'fill' vs
 * 'static' each mount/refresh (see "Mode dispatch" above).
 *
 * Lifecycle hooks —
 *
 * `onMount`/`onRefresh` (fn(cfg) => void, default no-ops) / `onUnmount`
 * (fn() => void, default no-op) — called after this layer's own mount/refresh/
 * unmount logic runs; every current caller uses these to add/remove a legend.
 *
 * URLs —
 *
 * `staticUrl` (fn(cfg) => string, default `${window.MAP_UI}/${cfg.outfile}`) — the
 * single-image URL used both by 'static' mode and by the WebGL-failure fallback.
 *
 * `hourDataUrl` (fn(cfg, hour, bust) => string|null, default resolves
 * `${outfile-without-.png}_f{hour:03d}_data.png?t={bust}`) — one forecast hour's raw
 * data texture URL. Returning a falsy value means "not resolvable yet" (e.g. a
 * currents-style reconciler still warming up) — skipped silently, no 404 and no
 * backfill flagged, unlike a real fetch failure.
 *
 * Backfill & layering —
 *
 * `backfillKey` (fn(snap) => {date, run, hour}|null, default `null`) — resolves
 * which forecast-hour identity to request backfill for when a per-hour texture
 * 404s, or when the reconcile loop's own freshness probe finds the image missing.
 *
 * `beforeId` (string MapLibre layer id, default `null`) — insert this layer beneath
 * `beforeId` if it currently exists; falls back to "add on top" otherwise rather
 * than throwing.
 *
 * Cache & refresh cadence —
 *
 * `cacheKey` (fn(cfg) => any, default `null`) — identifies which pre-rendered
 * variant `hourDataUrl` resolves to, for a layer with several backend-baked variants
 * of the same hour sharing one per-hour texture cache keyed by hour ALONE (e.g. a
 * species/mode selector). A `cacheKey` change on refresh clears the whole texture
 * cache so the next render re-fetches under the new variant's URL — the same clear
 * `onTimeline`'s own `bustChanged` branch does for a genuinely new render epoch.
 *
 * `refreshMs`/`syncMs` (numbers, undefined here — `liveLayerSync` supplies its own
 * defaults, 300000/20000ms, when omitted) — `refreshMs` is the slow-cadence refetch
 * interval while config is unchanged (picks up a backend re-render); `syncMs` is the
 * reconcile loop's own config-poll interval.
 *
 * Returns the reconcile loop's teardown handle (`liveLayerSync`'s return value) —
 * call it to unsubscribe from config polling and fully unmount this layer (its
 * `unmount` unsubscribes from the timeline and removes the MapLibre layer, whose
 * `onRemove` frees GL resources) before e.g. a basemap style swap.
 */

const PREFETCH_AHEAD = 3;
// How long to wait before re-fetching a per-hour texture that previously 404'd. Gives the
// demand-driven backfill time to fetch the field + render the PNG, then the layer retries
// (with a fresh cache-buster) so a backfilled hour appears without a manual reload.
const FAILED_RETRY_MS = 15000;
const MESH_COLS = 256;     // lon divisions of the globe fill mesh
const MESH_ROWS = 128;     // lat divisions (Mercator-clamped range)
const LAT_MAX = 85.051129; // Web Mercator limit (matches data texture extent)
// MapLibre's custom-layer API draws whatever geometry buildMesh() hands it through ONE
// projection matrix per frame -- unlike its built-in raster/vector tile layers, it does
// NOT automatically redraw a custom layer's geometry once per visible "world copy" when
// the viewport straddles the antimeridian (found live: a mesh built for a single -180..180
// span left a hard-edged gap beyond +-180 whenever the camera was centered near the
// dateline, e.g. New Zealand -- the fill simply had no geometry there to draw, distinct
// from (and in addition to) the texture-sampling seam TEXTURE_WRAP_S=REPEAT fixes below).
// Tiling the mesh across a few extra +-360 degree copies gives MapLibre's own projection
// math geometry to place correctly no matter which adjacent copy the current view needs,
// without having to special-case the camera's wrap offset ourselves. +-2 covers any
// single on-screen view that straddles the seam at any zoom this app allows (it never
// zooms out far enough to need more repeats than that).
const WORLD_COPIES = 2;

// Vertex shader: a lon/lat mesh vertex -> normalised mercator [0,1] -> projectTile.
// v_uv carries the equirectangular sample coord (x in [0,1] lon, y in [0,1] lat
// north->south) for the fragment shader to look up the data texture. No per-call
// template interpolation (unlike FS_BODY below), so it's a plain shared constant --
// used by both createFillLayer (hour-animated) and createStaticFillLayer (single,
// poll-refreshed texture).
const VS_BODY = `
precision highp float;
layout(location=0) in vec2 a_lonlat;   // degrees
out vec2 v_uv;
const float WF_PI = 3.141592653589793;
const float WF_LATMAX = 1.4844222297453324;   // mercator lat limit (rad)
vec2 toMerc(vec2 p){   // p = normalised lon[0..1], lat-fraction[0..1] (north->south)
    float lat = clamp((0.5 - p.y) * WF_PI, -WF_LATMAX, WF_LATMAX);
    float my = log(tan(WF_PI*0.25 + lat*0.5));
    return vec2(p.x, 0.5 - my/(2.0*WF_PI));
}
void main(){
    float nx = (a_lonlat.x + 180.0) / 360.0;          // 0..1 lon
    float latr = radians(a_lonlat.y);
    // normalised lat-fraction (north->south) from latitude
    float ny = 0.5 - (a_lonlat.y / 180.0);            // linear in degrees -> matches equirect data rows
    v_uv = vec2(nx, ny);
    vec4 clip = projectTile(toMerc(vec2(nx, ny)));
    gl_Position = clip;
}`;

// Build the lon/lat mesh (two triangles per cell), tiled across WORLD_COPIES' extra
// +-360 degree strips (see that constant's own docstring). Shared by createFillLayer
// (hour-animated) and createStaticFillLayer (single texture) -- identical geometry
// either way, since the seam-tiling need doesn't depend on how many textures a given
// fill variant samples.
function buildFillMesh(gl) {
    const verts = [];
    const dLon = 360 / MESH_COLS, dLat = (2 * LAT_MAX) / MESH_ROWS;
    for (let r = 0; r < MESH_ROWS; r++) {
        const lat0 = LAT_MAX - r * dLat, lat1 = LAT_MAX - (r + 1) * dLat;
        for (let w = -WORLD_COPIES; w <= WORLD_COPIES; w++) {
            const wOff = w * 360;
            for (let c = 0; c < MESH_COLS; c++) {
                const lon0 = -180 + wOff + c * dLon, lon1 = -180 + wOff + (c + 1) * dLon;
                verts.push(lon0, lat0, lon1, lat0, lon0, lat1,
                           lon0, lat1, lon1, lat0, lon1, lat1);
            }
        }
    }
    const meshVertCount = verts.length / 2;
    const meshBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, meshBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.STATIC_DRAW);
    return { meshBuf, meshVertCount };
}

// A blank (1x1, transparent) global data texture, parameterised for a LINEAR-filtered
// GPU sample of an always-global equirect field. REPEAT (not CLAMP_TO_EDGE) on S: the
// data texture's columns always span a complete 360 degrees (render is always global),
// so REPEAT lets the sampler wrap straight across the antimeridian instead of clamping
// to the edge texel, which produced a hard vertical break there (found live: isobars/
// precipitation/etc. static renders had already been fixed via close_lon_seam_for_contour,
// but that only closes the seam in the matplotlib PNG -- this GPU data texture, sampled
// by this shader, is a separate path with its own seam). T stays CLAMP_TO_EDGE: latitude
// is not cyclic (poles). Shared by createFillLayer's per-hour textures and
// createStaticFillLayer's single texture -- both are global LINEAR-filtered samples.
function initGlobalDataTexture(gl) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 0]));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return tex;
}

/** Resolve any CSS colour string ("White", "#07f", "rgb(...)") to [r,g,b] in 0..1. */
export function cssToRgb(str) {
    try {
        const c = document.createElement('canvas'); c.width = c.height = 1;
        const x = c.getContext('2d'); x.fillStyle = str || '#ffffff';
        x.fillRect(0, 0, 1, 1);
        const d = x.getImageData(0, 0, 1, 1).data;
        return [d[0] / 255, d[1] / 255, d[2] / 255];
    } catch { return [1, 1, 1]; }
}

/**
 * Chooses which per-hour texture entry(ies) render() should bind for this frame,
 * given the current-hour (e0) and next-hour (e1) cache entries' readiness. Exported
 * (pure, no GL/map dependency) so this decision is unit-testable without a full
 * WebGL/MapLibre harness -- this module otherwise has none.
 *
 * Prefers both hours ready (smooth inter-hour interpolation via u_frac, matching
 * lastSnap.playing/frac). If only ONE is ready, returns it for BOTH texture slots
 * with frac pinned to 0 -- mix() of identical inputs is that value regardless of
 * frac, so this is just the clearest way to say "show this one hour alone" -- rather
 * than null, so a still-catching-up neighbour hour (e.g. right after a render-
 * backlog-inducing cache clear, or simply the newest just-published hour with no
 * next-hour render yet) doesn't blank an otherwise-ready frame. Returns null only
 * when NEITHER hour is ready (nothing to draw at all).
 */
export function selectRenderTextures(e0, e1, playing, frac) {
    if (e0 && e0.ready && e1 && e1.ready) {
        return { texA: e0, texB: e1, frac: playing ? frac : 0.0 };
    }
    if (e0 && e0.ready) return { texA: e0, texB: e0, frac: 0.0 };
    if (e1 && e1.ready) return { texA: e1, texB: e1, frac: 0.0 };
    return null;
}

export function createFillLayer(map, opts) {
    const {
        sectionKey,
        initialConfig,
        vmin, vspan,
        fragmentBody,
        valueDecode = null,
        bicubic = false,
        customUniforms = () => ({}),
        opacity = 0.9,
        initialAnimation = {},
        initialCommon = {},
        onMount = () => {}, onRefresh = () => {}, onUnmount = () => {},
        backfillKey = null,
        beforeId = null,
        refreshMs, syncMs,
        staticUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile}`,
        hourDataUrl = (cfg, hour, bust) => {
            const base = cfg.outfile.replace(/\.png$/, '');
            const f = String(hour).padStart(3, '0');
            return `${window.MAP_UI}/${base}_f${f}_data.png?t=${bust}`;
        },
        forecastStepping = (anim) => (anim && anim.forecast_stepping !== false),
        cacheKey = null,
    } = opts;

    const S_SRC = `${sectionKey}-source`;
    const S_LYR = `${sectionKey}-layer`;
    const A_LYR = `${sectionKey}-fill-layer`;

    // addLayer, optionally beneath beforeId (only if that layer currently exists, so a
    // missing target degrades to "add on top" instead of throwing).
    const addBelow = (layerDef) =>
        map.addLayer(layerDef, (beforeId && map.getLayer(beforeId)) ? beforeId : undefined);

    let mode = null;                 // 'fill' | 'static'
    let webglFailed = false;
    let glRef = null;
    let progCache = new Map();       // keyed by MapLibre shader variant
    let progFailed = false;
    let meshBuf = null, meshVAO = null, meshVertCount = 0;
    let cmapTex = null, customLocs = {};
    let curAnim = initialAnimation || {}, curCommon = initialCommon || {}, curCfg = initialConfig || {};
    let curCacheKey = cacheKey ? cacheKey(curCfg) : null;
    let texSize = [1440, 721];

    // per-hour decoded-value textures (one single-frame texture per forecast hour)
    const texCache = new Map();
    let bustKey = timeline.get().refreshEpoch || Date.now();
    let unsubTimeline = null;
    let lastSnap = timeline.get();
    let layerAdded = false;

    // ---------- static fallback ----------
    const mountStatic = (cfg) => {
        if (map.getSource(S_SRC)) return;
        map.addSource(S_SRC, {
            type: 'image', url: `${staticUrl(cfg)}?t=${Date.now()}`,
            coordinates: [[-180, LAT_MAX], [180, LAT_MAX], [180, -LAT_MAX], [-180, -LAT_MAX]],
        });
        addBelow({ id: S_LYR, type: 'raster', source: S_SRC,
            paint: { 'raster-opacity': opacity, 'raster-fade-duration': 0 } });
    };
    const refreshStatic = (cfg) => {
        const s = map.getSource(S_SRC);
        if (s) s.updateImage({ url: `${staticUrl(cfg)}?t=${Date.now()}` });
    };
    const unmountStatic = () => {
        if (map.getLayer(S_LYR)) map.removeLayer(S_LYR);
        if (map.getSource(S_SRC)) map.removeSource(S_SRC);
    };

    // ---------- shaders ----------
    const FS_BODY = `
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex0;
uniform sampler2D u_tex1;
uniform float u_frac;
uniform float u_vmin;
uniform float u_span;
uniform vec2 u_texsize;
uniform sampler2D u_cmap;
float decodeNorm(vec4 d){ return ${valueDecode || '(d.r * 65280.0 + d.g * 255.0) / 65535.0'}; }
float tapVal(sampler2D t, vec2 uv){ return decodeNorm(texture(t, uv)); }
vec4 cubicW(float f){
    float f2=f*f, f3=f2*f;
    return vec4(-0.5*f3+f2-0.5*f, 1.5*f3-2.5*f2+1.0, -1.5*f3+2.0*f2+0.5*f, 0.5*f3-0.5*f2);
}
float bicubicVal(sampler2D t, vec2 uv){
    vec2 tsz=u_texsize; vec2 coord=uv*tsz-0.5; vec2 fxy=fract(coord);
    vec2 base=(coord-fxy+0.5)/tsz; vec4 wx=cubicW(fxy.x); vec4 wy=cubicW(fxy.y);
    float r=0.0;
    for(int j=0;j<4;j++){ float v=0.0;
        for(int i=0;i<4;i++){ vec2 off=vec2(float(i-1),float(j-1))/tsz; v+=wx[i]*tapVal(t,base+off);} 
        r+=wy[j]*v; }
    return r;
}
float sampleVal(sampler2D t, vec2 uv){ return ${bicubic ? 'bicubicVal(t, uv)' : 'decodeNorm(texture(t, uv))'}; }
${fragmentBody}
void main(){
    vec2 uv = v_uv;
    vec4 d0 = texture(u_tex0, uv);
    vec4 d1 = texture(u_tex1, uv);
    if (d0.a < 0.5 || d1.a < 0.5) discard;
    float value = mix(sampleVal(u_tex0, uv), sampleVal(u_tex1, uv), u_frac) * u_span + u_vmin;
    fragColor = shade(value, uv);
}`;

    const getProg = (gl, shaderData) => {
        const key = shaderData.variantName || '__default__';
        if (progCache.has(key)) return progCache.get(key);
        if (progFailed) return null;
        const vs = `#version 300 es\n${shaderData.vertexShaderPrelude}\n${shaderData.define}\n${VS_BODY}`;
        const fs = `#version 300 es\n${FS_BODY}`;
        const p = linkProg(gl, vs, fs, false, sectionKey);
        if (!p) { progFailed = true; return null; }
        progCache.set(key, p);
        return p;
    };

    const makeHourTexture = (gl, hour, bust = bustKey) => {
        const entry = { tex: initGlobalDataTexture(gl), ready: false, loading: true };
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
            if (!glRef || !texCache.has(hour)) return;
            glRef.bindTexture(glRef.TEXTURE_2D, entry.tex);
            glRef.texImage2D(glRef.TEXTURE_2D, 0, glRef.RGBA, glRef.RGBA, glRef.UNSIGNED_BYTE, img);
            if ((img.naturalWidth | 0) > 1) texSize = [img.naturalWidth, img.naturalHeight];
            entry.ready = true; entry.loading = false;
            map.triggerRepaint();
        };
        img.onerror = () => {
            entry.loading = false;
            entry.failedAt = Date.now();   // mark so getHourTexture can retry post-backfill
            // The per-hour texture 404'd (this fires on a scrub to a missing hour for an
            // already-mounted layer). Flag demand-driven backfill for THIS specific hour.
            flagBackfill(sectionKey, { ...timeline.get(), hour }, backfillKey);
        };
        const src = hourDataUrl(curCfg, hour, bust);
        if (!src) {                         // URL not resolvable yet (currents reconciler
            entry.loading = false;          // not ready) -> leave transparent, skip; no 404
            entry.failedAt = Date.now();    // allow retry once it resolves
            return entry;
        }
        img.src = src;
        return entry;
    };
    const getHourTexture = (hour) => {
        if (!glRef || hour < 0 || hour > lastSnap.maxHour) return null;
        let e = texCache.get(hour);
        // Retry a previously-failed hour (e.g. once a backfill has regenerated it). Re-fetch
        // with a FRESH per-hour cache-buster so the browser doesn't serve the cached 404 —
        // the frozen run-level bustKey alone wouldn't change the URL for the same filename.
        if (e && e.failedAt && (Date.now() - e.failedAt) >= FAILED_RETRY_MS) {
            texCache.delete(hour);
            e = null;
        }
        if (!e) { e = makeHourTexture(glRef, hour, Date.now()); texCache.set(hour, e); }
        return e;
    };
    const prefetch = (from) => { for (let k = 0; k <= PREFETCH_AHEAD; k++) { const h = from + k; if (h >= 0 && h <= lastSnap.maxHour) getHourTexture(h); } };
    const evict = (hour) => {
        const lo = hour - 1, hi = hour + PREFETCH_AHEAD;
        for (const [h, e] of texCache) if (h < lo || h > hi) { if (e.tex && glRef) glRef.deleteTexture(e.tex); texCache.delete(h); }
    };

    const uploadCmap = (gl, lut) => {
        if (!cmapTex) {
            cmapTex = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, cmapTex);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        }
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut);
    };

    // If the custom-layer shader fails to build at render time, fall back to the
    // static image once (deferred out of the render callback to avoid mutating
    // layers mid-render). webglFailed latches so we don't thrash.
    let fallbackPending = false;
    const requestFallback = () => {
        if (webglFailed || fallbackPending) return;
        fallbackPending = true;
        webglFailed = true;
        setTimeout(() => {
            fallbackPending = false;
            if (mode === 'fill') { unmountFill(); mountStatic(curCfg); mode = 'static'; }
        }, 0);
    };

    const onTimeline = (snap) => {
        const hourChanged = snap.hour !== lastSnap.hour;
        const bustChanged = snap.refreshEpoch !== bustKey;
        lastSnap = snap;
        if (bustChanged) {
            bustKey = snap.refreshEpoch;
            for (const [, e] of texCache) if (e.tex && glRef) glRef.deleteTexture(e.tex);
            texCache.clear();
        }
        if (hourChanged || bustChanged) { prefetch(snap.hour); evict(snap.hour); }
        map.triggerRepaint();
    };

    const layer = (cfg) => ({
        id: A_LYR, type: 'custom', renderingMode: '2d',
        onAdd(m, gl) {
            glRef = gl;
            progCache = new Map(); progFailed = false;
            ({ meshBuf, meshVertCount } = buildFillMesh(gl));
            bustKey = timeline.get().refreshEpoch || Date.now();
            lastSnap = timeline.get();
            // Upload the colour LUT now that we have a GL context (mountFill runs
            // before MapLibre calls onAdd, so glRef wasn't ready there).
            if (colormapOpt) { const lut = colormapOpt(curCfg); if (lut) uploadCmap(gl, lut); }
            prefetch(lastSnap.hour);
        },
        render(gl, args) {
            if (progFailed) { requestFallback(); return; }
            const prog = getProg(gl, args.shaderData);
            if (!prog) { requestFallback(); return; }
            const e0 = getHourTexture(lastSnap.hour);
            const e1 = getHourTexture(Math.min(lastSnap.maxHour, lastSnap.hour + 1));
            const sel = selectRenderTextures(e0, e1, lastSnap.playing, lastSnap.frac);
            if (!sel) { map.triggerRepaint(); return; }
            const { texA, texB, frac } = sel;

            gl.useProgram(prog);
            // MapLibre projection uniforms (globe/mercator), from args.
            const pd = args.defaultProjectionData;
            const U = (n) => gl.getUniformLocation(prog, n);
            gl.uniformMatrix4fv(U('u_projection_matrix'), false, pd.mainMatrix);
            gl.uniformMatrix4fv(U('u_projection_fallback_matrix'), false, pd.fallbackMatrix);
            gl.uniform4f(U('u_projection_clipping_plane'), pd.clippingPlane[0], pd.clippingPlane[1], pd.clippingPlane[2], pd.clippingPlane[3]);
            gl.uniform1f(U('u_projection_transition'), pd.projectionTransition);
            gl.uniform4f(U('u_projection_tile_mercator_coords'), pd.tileMercatorCoords[0], pd.tileMercatorCoords[1], pd.tileMercatorCoords[2], pd.tileMercatorCoords[3]);

            // data + colour
            gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texA.tex); gl.uniform1i(U('u_tex0'), 0);
            gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, texB.tex); gl.uniform1i(U('u_tex1'), 1);
            if (cmapTex) { gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, cmapTex); gl.uniform1i(U('u_cmap'), 2); }
            gl.uniform1f(U('u_frac'), frac);
            gl.uniform1f(U('u_vmin'), vmin);
            gl.uniform1f(U('u_span'), vspan);
            gl.uniform2f(U('u_texsize'), texSize[0], texSize[1]);
            // custom uniforms
            const cu = customUniforms(curCfg) || {};
            for (const [name, val] of Object.entries(cu)) {
                const loc = U(name);
                if (loc == null) continue;
                if (Array.isArray(val)) {
                    if (val.length === 2) gl.uniform2fv(loc, val);
                    else if (val.length === 3) gl.uniform3fv(loc, val);
                    else if (val.length === 4) gl.uniform4fv(loc, val);
                } else gl.uniform1f(loc, val);
            }

            gl.bindBuffer(gl.ARRAY_BUFFER, meshBuf);
            gl.enableVertexAttribArray(0);
            gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
            gl.disable(gl.DEPTH_TEST);
            gl.enable(gl.BLEND);
            gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
            gl.drawArrays(gl.TRIANGLES, 0, meshVertCount);

            if (lastSnap.playing) map.triggerRepaint();
        },
        onRemove(m, gl) {
            for (const [, e] of texCache) if (e.tex) gl.deleteTexture(e.tex);
            texCache.clear();
            if (cmapTex) gl.deleteTexture(cmapTex);
            if (meshBuf) gl.deleteBuffer(meshBuf);
            progCache.forEach((p) => gl.deleteProgram(p)); progCache.clear();
            cmapTex = meshBuf = null; glRef = null;
        },
    });

    // ---------- mount / refresh / unmount ----------
    const colormapOpt = opts.colormap || null;
    const mountFill = (cfg) => {
        if (layerAdded || map.getLayer(A_LYR)) return;
        curCfg = cfg; progFailed = false;
        addBelow(layer(cfg));
        layerAdded = true;
        if (progFailed) { unmountFill(); mountStatic(cfg); mode = 'static'; return; }
        // colour LUT is uploaded in onAdd (where gl is guaranteed); nothing to do here.
        unsubTimeline = timeline.subscribe(onTimeline);
        scrubber.layerActivated();
        map.triggerRepaint();
    };
    const refreshFill = (cfg) => {
        curCfg = cfg;
        if (cacheKey) {
            const newKey = cacheKey(cfg);
            if (newKey !== curCacheKey) {
                curCacheKey = newKey;
                // Same clear onTimeline's bustChanged branch does: hourDataUrl(cfg, ...)
                // now resolves to a different pre-rendered variant, so every cached
                // texture (fetched under the OLD key) is stale and must be re-fetched.
                for (const [, e] of texCache) if (e.tex && glRef) glRef.deleteTexture(e.tex);
                texCache.clear();
                if (glRef) prefetch(lastSnap.hour);
            }
        }
        if (colormapOpt && glRef) { const lut = colormapOpt(cfg); if (lut) uploadCmap(glRef, lut); }
        map.triggerRepaint();
    };
    const unmountFill = () => {
        if (unsubTimeline) { unsubTimeline(); unsubTimeline = null; }
        if (layerAdded) scrubber.layerDeactivated();
        if (map.getLayer(A_LYR)) map.removeLayer(A_LYR);   // fires onRemove cleanup
        layerAdded = false;
    };

    // ---------- dispatch: choose fill (forecast-stepping) vs static fallback ----------
    const wanted = () => (forecastStepping(curAnim) && !webglFailed) ? 'fill' : 'static';
    const switchTo = (target, cfg) => {
        if (mode === target) return;
        if (mode === 'static') unmountStatic(); else if (mode === 'fill') unmountFill();
        mode = target;
        if (target === 'fill') mountFill(cfg); else mountStatic(cfg);
    };
    const mount = (cfg, globals) => {
        curAnim = (globals && globals.animation) || {};
        curCommon = (globals && globals.common) || {};
        mode = wanted();
        if (mode === 'fill') mountFill(cfg); else mountStatic(cfg);
        onMount(cfg);
    };
    const refresh = (cfg, globals) => {
        curAnim = (globals && globals.animation) || {};
        curCommon = (globals && globals.common) || {};
        const want = wanted();
        if (want !== mode) switchTo(want, cfg);
        else if (mode === 'fill') refreshFill(cfg); else refreshStatic(cfg);
        onRefresh(cfg);
    };
    const unmount = () => {
        if (mode === 'static') unmountStatic(); else if (mode === 'fill') unmountFill();
        mode = null; onUnmount();
    };

    // Return the teardown so the host can fully clean up this fill layer before a
    // basemap style swap (its unmount unsubscribes from the timeline and removes the
    // layer, whose onRemove frees GL resources).
    return liveLayerSync(map, {
        sectionKey, initialConfig,
        initialGlobals: { animation: initialAnimation, common: initialCommon },
        globalKeys: ['animation', 'common'],
        mount, refresh, unmount,
        imageUrl: (cfg) => (forecastStepping(curAnim) && !webglFailed)
            ? hourDataUrl(cfg, timeline.get().hour, bustKey) : staticUrl(cfg),
        onMissing: () => flagBackfill(sectionKey, timeline.get(), backfillKey),
        refreshMs, syncMs,
    });
}

/**
 * GPU scalar-field fill for a layer with no forecast-hour dimension to animate --
 * SST/greenhouse_gases (issue #312) render once per cycle from their own independent
 * data sources (OISST/CAMS), not per GFS forecast hour, unlike every createFillLayer
 * consumer above. Forcing them through createFillLayer's per-hour texture cache and
 * shared animation timeline would couple them to a scrubber concept that doesn't
 * apply to them (the same frame would show for every hour) and would cost real,
 * pointless GPU/network churn (repainting on every animation tick of an unrelated
 * playing layer). This is deliberately a separate, smaller function rather than a
 * mode grafted onto createFillLayer -- see VS_BODY above for the one piece (the
 * projection vertex shader) that genuinely doesn't vary between the two and is
 * shared; the rest (mesh build, shader compile, projection-uniform wiring) is small
 * enough, and different enough in its surrounding lifecycle (single texture, no
 * prefetch/evict/interpolation, refreshed by liveLayerSync's plain poll instead of
 * timeline hour-changes), that extracting it too would cost more clarity than it
 * saves for two consumers. Revisit if a third non-hour-based raw-texture layer shows
 * up (this codebase's own "wait for a third occurrence" convention for shared code).
 *
 * Same shading contract as createFillLayer: fragmentBody defines `vec4 shade(float
 * value, vec2 uv)`, customUniforms/colormap are re-evaluated on every refresh (so a
 * palette or scale setting change never needs a server round-trip), physicalMin/
 * physicalSpan are the FIXED server-side encode_frames() domain (not the user's live
 * display range -- see e.g. tasks/sst.py's _ABS_ENCODE_VMIN/_ABS_ENCODE_VMAX).
 *
 * Land: `d.a < 0.5 -> discard` below (the same NaN-masked-cell convention every
 * texture-based layer uses) lets the basemap show through over land, rather than
 * painting a tint like the old matplotlib pipeline did. Live-verified (issue #312)
 * this reads clearly as land against the satellite basemap; deliberately not adding
 * a client-side land tint on top.
 */
export function createStaticFillLayer(map, opts) {
    const {
        sectionKey,
        initialConfig,
        // (cfg) => [physicalMin, physicalMax] -- a FUNCTION, not a fixed pair, unlike
        // createFillLayer's constant vmin/vspan: SST/greenhouse_gases' encode domain
        // depends on the live `mode` setting (absolute vs anomaly are two different
        // fixed domains -- see tasks/sst.py's _ABS_ENCODE_*/_ANOMALY_ENCODE_*), so it
        // must be resolved fresh against curCfg on every render, not captured once.
        physicalDomain,
        fragmentBody,
        valueDecode = null,
        customUniforms = () => ({}),
        colormap = null,
        beforeId = null,
        onMount = () => {}, onRefresh = () => {}, onUnmount = () => {},
        dataUrl,
        refreshMs, syncMs,
    } = opts;

    const S_LYR = `${sectionKey}-static-fill-layer`;
    const addBelow = (layerDef) =>
        map.addLayer(layerDef, (beforeId && map.getLayer(beforeId)) ? beforeId : undefined);

    let glRef = null;
    let progCache = new Map();   // keyed by MapLibre shader variant, same as createFillLayer
    let progFailed = false;
    let meshBuf = null, meshVertCount = 0;
    let dataTex = null, dataReady = false, loadSeq = 0;
    let cmapTex = null;
    let curCfg = initialConfig || {};
    let layerAdded = false;

    const FS_BODY = `
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_vmin;
uniform float u_span;
uniform sampler2D u_cmap;
float decodeNorm(vec4 d){ return ${valueDecode || '(d.r * 65280.0 + d.g * 255.0) / 65535.0'}; }
${fragmentBody}
void main(){
    vec4 d = texture(u_tex, v_uv);
    if (d.a < 0.5) discard;
    float value = decodeNorm(d) * u_span + u_vmin;
    fragColor = shade(value, v_uv);
}`;

    // shaderData supplies MapLibre's projectTile() prelude (globe/mercator variant) --
    // without it the VS_BODY call to projectTile() has nothing defining it. Cached per
    // variantName like createFillLayer.getProg, since MapLibre swaps variants across a
    // globe/mercator projection transition.
    const getProg = (gl, shaderData) => {
        const key = shaderData.variantName || '__default__';
        if (progCache.has(key)) return progCache.get(key);
        if (progFailed) return null;
        const vs = `#version 300 es\n${shaderData.vertexShaderPrelude}\n${shaderData.define}\n${VS_BODY}`;
        const fs = `#version 300 es\n${FS_BODY}`;
        const p = linkProg(gl, vs, fs, false, sectionKey);
        if (!p) { progFailed = true; return null; }
        progCache.set(key, p);
        return p;
    };

    const uploadCmap = (gl, lut) => {
        if (!cmapTex) {
            cmapTex = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, cmapTex);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        }
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut);
    };

    // seq guards against an in-flight image load from a SUPERSEDED refresh() call
    // finishing after a newer one and overwriting fresher data with stale bytes.
    const loadDataTexture = (gl, url) => {
        if (!dataTex) {
            dataTex = initGlobalDataTexture(gl);
        }
        const seq = ++loadSeq;
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
            if (!glRef || seq !== loadSeq) return;
            glRef.bindTexture(glRef.TEXTURE_2D, dataTex);
            glRef.texImage2D(glRef.TEXTURE_2D, 0, glRef.RGBA, glRef.RGBA, glRef.UNSIGNED_BYTE, img);
            dataReady = true;
            map.triggerRepaint();
        };
        img.onerror = () => {};
        img.src = url;
    };

    const layer = () => ({
        id: S_LYR, type: 'custom', renderingMode: '2d',
        onAdd(m, gl) {
            glRef = gl;
            progCache = new Map(); progFailed = false;
            ({ meshBuf, meshVertCount } = buildFillMesh(gl));
            if (colormap) { const lut = colormap(curCfg); if (lut) uploadCmap(gl, lut); }
            loadDataTexture(gl, `${dataUrl(curCfg)}?t=${Date.now()}`);
        },
        render(gl, args) {
            const p = getProg(gl, args.shaderData);
            if (!p || !dataReady) return;
            gl.useProgram(p);
            const pd = args.defaultProjectionData;
            const U = (n) => gl.getUniformLocation(p, n);
            gl.uniformMatrix4fv(U('u_projection_matrix'), false, pd.mainMatrix);
            gl.uniformMatrix4fv(U('u_projection_fallback_matrix'), false, pd.fallbackMatrix);
            gl.uniform4f(U('u_projection_clipping_plane'), pd.clippingPlane[0], pd.clippingPlane[1], pd.clippingPlane[2], pd.clippingPlane[3]);
            gl.uniform1f(U('u_projection_transition'), pd.projectionTransition);
            gl.uniform4f(U('u_projection_tile_mercator_coords'), pd.tileMercatorCoords[0], pd.tileMercatorCoords[1], pd.tileMercatorCoords[2], pd.tileMercatorCoords[3]);

            gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, dataTex); gl.uniform1i(U('u_tex'), 0);
            if (cmapTex) { gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, cmapTex); gl.uniform1i(U('u_cmap'), 1); }
            const [domMin, domMax] = physicalDomain(curCfg);
            gl.uniform1f(U('u_vmin'), domMin);
            gl.uniform1f(U('u_span'), domMax - domMin);
            const cu = customUniforms(curCfg) || {};
            for (const [name, val] of Object.entries(cu)) {
                const loc = U(name);
                if (loc == null) continue;
                if (Array.isArray(val)) {
                    if (val.length === 2) gl.uniform2fv(loc, val);
                    else if (val.length === 3) gl.uniform3fv(loc, val);
                    else if (val.length === 4) gl.uniform4fv(loc, val);
                } else gl.uniform1f(loc, val);
            }

            gl.bindBuffer(gl.ARRAY_BUFFER, meshBuf);
            gl.enableVertexAttribArray(0);
            gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
            gl.disable(gl.DEPTH_TEST);
            gl.enable(gl.BLEND);
            gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
            gl.drawArrays(gl.TRIANGLES, 0, meshVertCount);
        },
        onRemove(m, gl) {
            if (dataTex) gl.deleteTexture(dataTex);
            if (cmapTex) gl.deleteTexture(cmapTex);
            if (meshBuf) gl.deleteBuffer(meshBuf);
            progCache.forEach((p) => gl.deleteProgram(p)); progCache.clear();
            dataTex = cmapTex = meshBuf = null;
            dataReady = false; glRef = null;
        },
    });

    const mount = (cfg) => {
        curCfg = cfg;
        if (layerAdded || map.getLayer(S_LYR)) return;
        addBelow(layer());
        layerAdded = true;
        onMount(cfg);
    };
    const refresh = (cfg) => {
        curCfg = cfg;
        if (glRef) {
            if (colormap) { const lut = colormap(cfg); if (lut) uploadCmap(glRef, lut); }
            loadDataTexture(glRef, `${dataUrl(cfg)}?t=${Date.now()}`);
        }
        onRefresh(cfg);
    };
    const unmount = () => {
        if (layerAdded && map.getLayer(S_LYR)) map.removeLayer(S_LYR);   // fires onRemove cleanup
        layerAdded = false;
        onUnmount();
    };

    return liveLayerSync(map, {
        sectionKey, initialConfig, mount, refresh, unmount,
        imageUrl: (cfg) => dataUrl(cfg),
        refreshMs, syncMs,
    });
}