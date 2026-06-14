import { liveLayerSync } from './_refresh.js';
import { timeline } from './timeline.js';
import { scrubber } from './scrubber.js';

/**
 * Shared machinery for GPU-animated raster overlays — HOURLY SCRUBBER edition.
 *
 * Instead of baking N forecast frames into one filmstrip texture and looping on a
 * wall clock, each layer now fetches per-hour data textures `{layer}_f{NNN}_data.png`
 * and is driven by the shared `timeline` (play/pause/step). Two single-hour textures
 * are resident at a time — the current hour and the next — and the fragment shader
 * cross-fades between them by `u_frac` (0..1) while playing; on a paused/stepped
 * state frac is 0 so you see the exact hour.
 *
 * Falls back to the legacy static `image` raster when `animated` is off, WebGL2 is
 * unavailable, or a data texture cannot be loaded.
 *
 * A layer supplies: value range (vmin/vspan), a fragment `shade()` body, and any
 * custom uniforms. Everything else is generic.
 */

const MERCATOR_CORNERS = [
    [-180, 85.051129], [180, 85.051129],
    [180, -85.051129], [-180, -85.051129],
];

const PREFETCH_AHEAD = 3;   // hours to prefetch beyond the current one

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

const VERT = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() {
    v_uv = vec2(a_pos.x, 1.0 - a_pos.y);   // v=0 at top (north)
    gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0);
}`;

// Caller's `body` declares extra uniforms and defines:
//     vec4 shade(float value, vec2 uv)   // returns STRAIGHT-alpha rgba
// Two single-hour textures (u_tex0 = current hour, u_tex1 = next hour) are
// cross-faded by u_frac. R = normalised value, A = mask.
const fragSource = (body, valueDecode, bicubic) => `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex0;          // current hour
uniform sampler2D u_tex1;          // next hour
uniform float u_frac;              // 0..1 cross-fade current->next
uniform float u_vmin;
uniform float u_span;
uniform vec2 u_texsize;            // data texture dimensions (px), for bicubic taps
uniform sampler2D u_cmap;          // optional 256x1 colour LUT (unused by some layers)
const float PI = 3.141592653589793;
// Per-layer normalised-value extractor from a texel. Default: 16-bit, R=high byte,
// G=low byte (65535 levels) — matches encode_frames' default and removes value
// stepping. A layer can override valueDecode (e.g. 'd.r' for a legacy 8-bit texture).
float decodeNorm(vec4 d) { return ${valueDecode || '(d.r * 65280.0 + d.g * 255.0) / 65535.0'}; }

// Sample one texture's DECODED value (nearest texel). Used as the bicubic tap.
float tapVal(sampler2D t, vec2 uv) { return decodeNorm(texture(t, uv)); }

// Catmull-Rom cubic weights for fractional position f (one axis).
vec4 cubicW(float f) {
    float f2 = f * f, f3 = f2 * f;
    return vec4(
        -0.5*f3 + f2 - 0.5*f,
         1.5*f3 - 2.5*f2 + 1.0,
        -1.5*f3 + 2.0*f2 + 0.5*f,
         0.5*f3 - 0.5*f2
    );
}

// Bicubic interpolation of the DECODED value (16 taps). Interpolating the decoded
// scalar — not raw bytes — keeps 16-bit layers correct. Smooth gradient across
// cell boundaries -> smooth band contours even at high zoom (no resolution bump).
float bicubicVal(sampler2D t, vec2 uv) {
    vec2 tsz = u_texsize;
    vec2 coord = uv * tsz - 0.5;
    vec2 fxy = fract(coord);
    vec2 base = (coord - fxy + 0.5) / tsz;     // texel-centre of the -1 sample
    vec4 wx = cubicW(fxy.x);
    vec4 wy = cubicW(fxy.y);
    float result = 0.0;
    for (int j = 0; j < 4; j++) {
        float v = 0.0;
        for (int i = 0; i < 4; i++) {
            vec2 off = vec2(float(i - 1), float(j - 1)) / tsz;
            v += wx[i] * tapVal(t, base + off);
        }
        result += wy[j] * v;
    }
    return result;
}

// Decoded value for a tap, honouring the per-layer interpolation mode.
float sampleVal(sampler2D t, vec2 uv) {
    return ${bicubic ? 'bicubicVal(t, uv)' : 'decodeNorm(texture(t, uv))'};
}
${body}
void main() {
    float latRad = atan(sinh(PI * (1.0 - 2.0 * v_uv.y)));   // mercator row -> latitude
    float texV = (PI * 0.5 - latRad) / PI;                  // -> equirectangular V
    vec2 uv = vec2(v_uv.x, texV);
    // Mask still read from the nearest texel of each hour (alpha is binary).
    vec4 d0 = texture(u_tex0, uv);
    vec4 d1 = texture(u_tex1, uv);
    if (d0.a < 0.5 || d1.a < 0.5) discard;                  // missing data
    float value = mix(sampleVal(u_tex0, uv), sampleVal(u_tex1, uv), u_frac) * u_span + u_vmin;
    fragColor = shade(value, uv);
}`;

export function createAnimatedRasterLayer(map, opts) {
    const {
        sectionKey,
        initialConfig,
        coordinates = MERCATOR_CORNERS,
        vmin, vspan,
        fragmentBody,
        valueDecode = null,           // optional GLSL expr over vec4 'd' (e.g. 16-bit decode)
        bicubic = false,              // bicubic value interpolation (smooth contours at high zoom)
        customUniforms = () => ({}),
        opacity = 0.85,
        initialAnimation = {},
        initialCommon = {},
        resolution = (cfg) => {
            const lod = parseInt(cfg.level_of_detail, 10);
            const w = lod === 1 ? 2048 : lod === 3 ? 8192 : 4096;   // default (2) -> 4096
            return { w, h: Math.round(w / 2) };
        },
        resampling = (anim) => (anim.sharp ? 'nearest' : 'linear'),
        colormap = null,
        onMount = () => {}, onRefresh = () => {}, onUnmount = () => {},
        refreshMs, syncMs,
        staticUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile}`,
        // Per-hour data texture URL. cfg.outfile is like "data/isobars.png";
        // we inject the _fNNN before _data.png -> "data/isobars_f006_data.png".
        hourDataUrl = (cfg, hour, bust) => {
            const base = cfg.outfile.replace(/\.png$/, '');
            const f = String(hour).padStart(3, '0');
            return `${window.MAP_UI}/${base}_f${f}_data.png?t=${bust}`;
        },
        // Forecast stepping is a GLOBAL setting, not per-layer. When on, this layer
        // runs the GPU scrubber (timeline-driven, per-hour textures). When off, it
        // shows the current hour (f000) only, via the static base-name image.
        // (Particle layers like wind/waves are a separate visualisation methodology
        // and are NOT gated by this — they live in their own modules.)
        forecastStepping = (anim) => (anim && anim.forecast_stepping !== false),
    } = opts;

    const S_SRC = `${sectionKey}-source`;
    const S_LYR = `${sectionKey}-layer`;
    const A_SRC = `${sectionKey}-anim-source`;
    const A_LYR = `${sectionKey}-anim-layer`;

    let mode = null;               // 'static' | 'animated'
    let webglFailed = false;
    let glCanvas = null, gl = null, program = null, quadBuf = null, aPos = -1;
    let outCanvas = null, out2d = null;
    let rafId = null, drawPending = true;
    let uTex0 = null, uTex1 = null, uFrac = null, uCmap = null, customLocs = {};
    let cmapTex = null;
    let curW = 2048, curH = 1024, curResampling = 'linear';
    let curAnim = initialAnimation || {};
    let curCommon = initialCommon || {};
    let curCfg = initialConfig || {};

    // Per-hour texture cache: hour -> { tex, ready, loading }
    const texCache = new Map();
    let bustKey = timeline.get().refreshEpoch || Date.now();
    let unsubTimeline = null;
    let lastSnap = { hour: 0, frac: 0, playing: false, maxHour: 23 };

    // ---------- static ----------
    const mountStatic = (cfg) => {
        if (map.getSource(S_SRC)) return;
        map.addSource(S_SRC, { type: 'image', url: `${staticUrl(cfg)}?t=${Date.now()}`, coordinates });
        map.addLayer({ id: S_LYR, type: 'raster', source: S_SRC,
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

    // ---------- animated ----------
    const compile = (type, src) => {
        const sh = gl.createShader(type);
        gl.shaderSource(sh, src); gl.compileShader(sh);
        if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
            console.warn(`[${sectionKey}] shader compile failed:`, gl.getShaderInfoLog(sh));
            return null;
        }
        return sh;
    };
    const initGL = () => {
        glCanvas = document.createElement('canvas');
        glCanvas.width = curW; glCanvas.height = curH;
        gl = glCanvas.getContext('webgl2', { premultipliedAlpha: false, antialias: true });
        if (!gl) return false;
        const vs = compile(gl.VERTEX_SHADER, VERT);
        const fs = compile(gl.FRAGMENT_SHADER, fragSource(fragmentBody, valueDecode, bicubic));
        if (!vs || !fs) return false;
        program = gl.createProgram();
        gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
            console.warn(`[${sectionKey}] program link failed:`, gl.getProgramInfoLog(program));
            return false;
        }
        quadBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
        aPos = gl.getAttribLocation(program, 'a_pos');
        uTex0 = gl.getUniformLocation(program, 'u_tex0');
        uTex1 = gl.getUniformLocation(program, 'u_tex1');
        uFrac = gl.getUniformLocation(program, 'u_frac');
        uCmap = gl.getUniformLocation(program, 'u_cmap');
        gl.useProgram(program);
        gl.uniform1f(gl.getUniformLocation(program, 'u_vmin'), vmin);
        gl.uniform1f(gl.getUniformLocation(program, 'u_span'), vspan);
        // Default data-texture size (GFS 0.25° source grid); refined on image load.
        const tsLoc = gl.getUniformLocation(program, 'u_texsize');
        if (tsLoc) gl.uniform2f(tsLoc, 1440.0, 721.0);

        // Colour LUT texture (256x1). Defaults to white.
        cmapTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
            new Uint8Array([255, 255, 255, 255]));

        outCanvas = document.createElement('canvas');
        outCanvas.id = `${sectionKey}-anim-canvas`;
        outCanvas.width = curW; outCanvas.height = curH;
        Object.assign(outCanvas.style, { position: 'absolute', top: '-10000px', width: '1px', height: '1px' });
        document.body.appendChild(outCanvas);
        out2d = outCanvas.getContext('2d');
        return true;
    };
    const setUniform = (loc, val) => {
        if (loc == null) return;
        if (Array.isArray(val)) {
            if (val.length === 2) gl.uniform2fv(loc, val);
            else if (val.length === 3) gl.uniform3fv(loc, val);
            else if (val.length === 4) gl.uniform4fv(loc, val);
        } else { gl.uniform1f(loc, val); }
    };
    const applyCustomUniforms = (cfg) => {
        if (!gl || !program) return;
        gl.useProgram(program);
        const cu = customUniforms(cfg) || {};
        for (const [name, val] of Object.entries(cu)) {
            if (!(name in customLocs)) customLocs[name] = gl.getUniformLocation(program, name);
            setUniform(customLocs[name], val);
        }
    };
    const colourise = (cfg) => {
        if (!gl || !cmapTex || !colormap) return;
        const lut = colormap(cfg);
        if (!lut) return;
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut);
    };

    // Build a GL texture for one forecast hour, loading its PNG.
    const makeHourTexture = (hour) => {
        const entry = { tex: gl.createTexture(), ready: false, loading: true };
        gl.bindTexture(gl.TEXTURE_2D, entry.tex);
        // 1x1 transparent placeholder until the image arrives.
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
            new Uint8Array([0, 0, 0, 0]));
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
            if (!gl || !texCache.has(hour)) return;   // unmounted/evicted meanwhile
            gl.bindTexture(gl.TEXTURE_2D, entry.tex);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
            entry.ready = true; entry.loading = false;
            // Record the data-texture size for bicubic tap offsets (all hours share
            // the same source grid; set once when the first image arrives).
            if (bicubic && program && (img.naturalWidth | 0) > 1) {
                gl.useProgram(program);
                const loc = gl.getUniformLocation(program, 'u_texsize');
                if (loc) gl.uniform2f(loc, img.naturalWidth, img.naturalHeight);
            }
            drawPending = true;
        };
        img.onerror = () => {
            entry.loading = false;   // leave ready=false; treated as not-available
        };
        img.src = hourDataUrl(curCfg, hour, bustKey);
        return entry;
    };

    const getHourTexture = (hour) => {
        if (hour < 0 || hour > lastSnap.maxHour) return null;
        let entry = texCache.get(hour);
        if (!entry) {
            entry = makeHourTexture(hour);
            texCache.set(hour, entry);
        }
        return entry;
    };

    const prefetch = (fromHour) => {
        for (let k = 0; k <= PREFETCH_AHEAD; k++) {
            const h = fromHour + k;
            if (h >= 0 && h <= lastSnap.maxHour) getHourTexture(h);
        }
    };

    // Drop textures outside [hour-1 .. hour+PREFETCH_AHEAD] to bound GPU memory.
    const evict = (hour) => {
        const lo = hour - 1, hi = hour + PREFETCH_AHEAD;
        for (const [h, entry] of texCache) {
            if (h < lo || h > hi) {
                if (entry.tex && gl) gl.deleteTexture(entry.tex);
                texCache.delete(h);
            }
        }
    };

    const drawOffscreen = (snap) => {
        const h0 = snap.hour;
        const h1 = Math.min(snap.maxHour, snap.hour + 1);
        const e0 = getHourTexture(h0);
        const e1 = getHourTexture(h1);
        // Need both current & next ready to draw a clean frame.
        if (!e0 || !e1 || !e0.ready || !e1.ready) return false;

        gl.viewport(0, 0, glCanvas.width, glCanvas.height);
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.useProgram(program);
        gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
        gl.enableVertexAttribArray(aPos);
        gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, e0.tex);
        gl.uniform1i(uTex0, 0);
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, e1.tex);
        gl.uniform1i(uTex1, 1);
        gl.activeTexture(gl.TEXTURE2);
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(uCmap, 2);

        gl.uniform1f(uFrac, snap.playing ? snap.frac : 0.0);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        return true;
    };

    // rAF blit loop: keeps the MapLibre canvas source fed. Only redraws when the
    // timeline moved (drawPending) or while playing (continuous cross-fade).
    const loop = () => {
        if (gl && program) {
            if (drawPending || lastSnap.playing) {
                if (drawOffscreen(lastSnap)) {
                    out2d.clearRect(0, 0, outCanvas.width, outCanvas.height);
                    out2d.drawImage(glCanvas, 0, 0);
                    drawPending = false;
                }
            }
        }
        rafId = requestAnimationFrame(loop);
    };

    // Timeline subscription: react to hour/frac/play changes.
    const onTimeline = (snap) => {
        const hourChanged = snap.hour !== lastSnap.hour;
        const bustChanged = snap.refreshEpoch !== bustKey;
        lastSnap = snap;
        if (bustChanged) {
            // Data refresh: stale textures -> rebuild for the held hour.
            bustKey = snap.refreshEpoch;
            for (const [, entry] of texCache) { if (entry.tex && gl) gl.deleteTexture(entry.tex); }
            texCache.clear();
        }
        if (hourChanged || bustChanged) {
            prefetch(snap.hour);
            evict(snap.hour);
        }
        drawPending = true;
    };

    const mountAnimated = (cfg) => {
        if (map.getSource(A_SRC)) return;
        curCfg = cfg;
        const res = resolution(cfg); curW = res.w; curH = res.h;
        curResampling = resampling(curAnim);
        if (!initGL()) { webglFailed = true; cleanupGL(); mountStatic(cfg); mode = 'static'; return; }
        applyCustomUniforms(cfg);
        colourise(cfg);
        bustKey = timeline.get().refreshEpoch || Date.now();
        lastSnap = timeline.get();
        prefetch(lastSnap.hour);
        map.addSource(A_SRC, { type: 'canvas', canvas: outCanvas, animate: true, coordinates });
        map.addLayer({ id: A_LYR, type: 'raster', source: A_SRC,
            paint: { 'raster-opacity': opacity, 'raster-fade-duration': 0, 'raster-resampling': curResampling } });
        unsubTimeline = timeline.subscribe(onTimeline);
        scrubber.layerActivated();
        drawPending = true;
        rafId = requestAnimationFrame(loop);
    };
    const refreshAnimated = (cfg) => {
        const res = resolution(cfg);
        if (res.w !== curW || res.h !== curH) {   // LOD change -> rebuild GL
            unmountAnimated(); mountAnimated(cfg); return;
        }
        curCfg = cfg;
        const rs = resampling(curAnim);
        if (rs !== curResampling) {
            curResampling = rs;
            if (map.getLayer(A_LYR)) map.setPaintProperty(A_LYR, 'raster-resampling', rs);
        }
        applyCustomUniforms(cfg);
        colourise(cfg);
        drawPending = true;
    };
    const cleanupGL = () => {
        if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
        if (unsubTimeline) { unsubTimeline(); unsubTimeline = null; }
        if (gl) {
            for (const [, entry] of texCache) { if (entry.tex) gl.deleteTexture(entry.tex); }
            texCache.clear();
            if (cmapTex) gl.deleteTexture(cmapTex);
            if (quadBuf) gl.deleteBuffer(quadBuf);
            if (program) gl.deleteProgram(program);
            gl.getExtension('WEBGL_lose_context')?.loseContext();
        }
        if (outCanvas && outCanvas.parentNode) outCanvas.parentNode.removeChild(outCanvas);
        gl = null; glCanvas = null; outCanvas = null; out2d = null;
        program = null; quadBuf = null; cmapTex = null; customLocs = {};
    };
    const unmountAnimated = () => {
        scrubber.layerDeactivated();
        if (map.getLayer(A_LYR)) map.removeLayer(A_LYR);
        if (map.getSource(A_SRC)) map.removeSource(A_SRC);
        cleanupGL();
    };

    // ---------- dispatch ----------
    const wanted = () => (forecastStepping(curAnim) && !webglFailed) ? 'animated' : 'static';
    const switchTo = (target, cfg) => {
        if (mode === target) return;
        if (mode === 'static') unmountStatic();
        else if (mode === 'animated') unmountAnimated();
        mode = target;
        if (target === 'animated') mountAnimated(cfg); else mountStatic(cfg);
    };
    const mount = (cfg, globals) => {
        curAnim = (globals && globals.animation) || {};
        curCommon = (globals && globals.common) || {};
        mode = wanted();
        if (mode === 'animated') mountAnimated(cfg); else mountStatic(cfg);
        onMount(cfg);
    };
    const refresh = (cfg, globals) => {
        curAnim = (globals && globals.animation) || {};
        curCommon = (globals && globals.common) || {};
        const want = wanted();
        if (want !== mode) switchTo(want, cfg);
        else if (mode === 'animated') refreshAnimated(cfg); else refreshStatic(cfg);
        onRefresh(cfg);
    };
    const unmount = () => {
        if (mode === 'static') unmountStatic();
        else if (mode === 'animated') unmountAnimated();
        mode = null;
        onUnmount();
    };

    liveLayerSync(map, {
        sectionKey, initialConfig,
        initialGlobals: { animation: initialAnimation, common: initialCommon },
        globalKeys: ['animation', 'common'],
        mount, refresh, unmount,
        // Probe the current hour's texture (stepping on) or the static base (off).
        imageUrl: (cfg) => (forecastStepping(curAnim) && !webglFailed)
            ? hourDataUrl(cfg, timeline.get().hour, bustKey)
            : staticUrl(cfg),
        refreshMs, syncMs,
    });
}