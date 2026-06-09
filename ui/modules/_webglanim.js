import { liveLayerSync } from './_refresh.js';
import { forecastHud } from './forecast_progress.js';

/**
 * Shared machinery for GPU-animated raster overlays.
 *
 * Renders the interpolated field (R=now, G=+next from the `*_data.png` data
 * texture) into an offscreen WebGL2 canvas in Web-Mercator, blits it to a 2D
 * canvas, and shows that via a MapLibre `canvas` source using the standard
 * mercator corner coordinates — so MapLibre projects it onto the globe exactly
 * as it does the static image layers. The WebGL->2D blit happens in the same
 * animation tick as the draw (so the drawing buffer is always valid) and a 2D
 * canvas is what MapLibre samples (the well-supported path).
 *
 * Falls back to the legacy static `image` raster when `animated` is off, WebGL2
 * is unavailable, or the data texture cannot be loaded.
 *
 * A layer supplies: value range (vmin/vspan), a fragment `shade()` body, and
 * any custom uniforms. Everything else is generic.
 */

const MERCATOR_CORNERS = [
    [-180, 85.051129], [180, 85.051129],
    [180, -85.051129], [-180, -85.051129],
];

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
const fragSource = (body) => `#version 300 es
precision highp float;
precision highp sampler2DArray;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2DArray u_frames;   // N forecast frames stacked as array layers; R=value, A=mask
uniform float u_time;              // 0..1 across the whole forecast span
uniform float u_frames_n;          // number of frames (>= 2)
uniform float u_vmin;
uniform float u_span;
uniform sampler2D u_cmap;          // optional 256x1 colour LUT (unused by some layers)
const float PI = 3.141592653589793;
${body}
void main() {
    float latRad = atan(sinh(PI * (1.0 - 2.0 * v_uv.y)));   // mercator row -> latitude
    float texV = (PI * 0.5 - latRad) / PI;                  // -> equirectangular V
    vec2 uv = vec2(v_uv.x, texV);
    float seg = u_time * (u_frames_n - 1.0);                // 0 .. N-1
    float i0 = floor(seg);
    float frac = seg - i0;
    float i1 = min(i0 + 1.0, u_frames_n - 1.0);
    vec4 d0 = texture(u_frames, vec3(uv, i0));              // array layer = nearest integer
    vec4 d1 = texture(u_frames, vec3(uv, i1));
    if (d0.a < 0.5 || d1.a < 0.5) discard;                  // missing data
    float value = mix(d0.r, d1.r, frac) * u_span + u_vmin;
    fragColor = shade(value, uv);
}`;

export function createAnimatedRasterLayer(map, opts) {
    const {
        sectionKey,
        initialConfig,
        coordinates = MERCATOR_CORNERS,
        vmin, vspan,
        fragmentBody,
        customUniforms = () => ({}),
        opacity = 0.85,
        // Initial snapshot of the global [animation] section (from page-load config),
        // so the first mount slices the texture with the correct frame count.
        initialAnimation = {},
        // Initial [common] snapshot — supplies forecast_hour for the progress HUD start.
        initialCommon = {},
        // Loop length in seconds (from [animation].seconds). Shared across layers.
        loopSeconds = (anim) => (Number(anim.seconds) > 0 ? Number(anim.seconds) : 8),
        // Offscreen canvas size, driven by the per-layer Low/Medium/High `level_of_detail`
        // selector (1->2048, 2->4096, 3->8192). Override per-layer if needed.
        resolution = (cfg) => {
            const lod = parseInt(cfg.level_of_detail, 10);
            const w = lod === 1 ? 2048 : lod === 3 ? 8192 : 4096;   // default (2) -> 4096
            return { w, h: Math.round(w / 2) };
        },
        // 'nearest' = crisp but can shimmer while moving; 'linear' = smooth (default).
        resampling = (anim) => (anim.sharp ? 'nearest' : 'linear'),
        // number of forecast frames packed into the data texture (>= 2)
        frames = (anim) => Math.max(2, parseInt(anim.frames, 10) || 2),
        // true = ping-pong (now->ahead->now, seamless); false = forward then reset.
        bounce = (anim) => !!anim.bounce,
        // optional colour LUT: (cfg) -> Uint8Array(256*4) | null  (sampled as u_cmap)
        colormap = null,
        // optional side-content hooks (e.g. legends), fired in both static & animated modes
        onMount = () => {}, onRefresh = () => {}, onUnmount = () => {},
        refreshMs, syncMs,             // optional; undefined -> liveLayerSync defaults
        staticUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile}`,
        dataUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile.replace(/\.png$/, '_data.png')}`,
        isAnimated = (cfg) => !!cfg.animated,
    } = opts;

    const S_SRC = `${sectionKey}-source`;
    const S_LYR = `${sectionKey}-layer`;
    const A_SRC = `${sectionKey}-anim-source`;
    const A_LYR = `${sectionKey}-anim-layer`;

    let mode = null;               // 'static' | 'animated'
    let webglFailed = false;
    let glCanvas = null, gl = null, program = null, quadBuf = null, aPos = -1;
    let outCanvas = null, out2d = null;
    let framesTex = null, texReady = false, rafId = null;
    let uTime = null, uFrames = null, uFramesN = null, uCmap = null, customLocs = {};
    let cmapTex = null;
    let curW = 2048, curH = 1024, curResampling = 'linear', curN = 2, curBounce = false;
    let curAnim = initialAnimation || {};   // latest global [animation] section
    let curCommon = initialCommon || {};    // latest global [common] section
    let loopMs = 8000;

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
        glCanvas.width = curW; glCanvas.height = curH;          // WebGL render target (detached is fine)
        gl = glCanvas.getContext('webgl2', { premultipliedAlpha: false, antialias: true });
        if (!gl) return false;
        const vs = compile(gl.VERTEX_SHADER, VERT);
        const fs = compile(gl.FRAGMENT_SHADER, fragSource(fragmentBody));
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
        uTime = gl.getUniformLocation(program, 'u_time');
        uFrames = gl.getUniformLocation(program, 'u_frames');
        uFramesN = gl.getUniformLocation(program, 'u_frames_n');
        uCmap = gl.getUniformLocation(program, 'u_cmap');
        gl.useProgram(program);
        gl.uniform1f(gl.getUniformLocation(program, 'u_vmin'), vmin);
        gl.uniform1f(gl.getUniformLocation(program, 'u_span'), vspan);
        gl.uniform1f(uFramesN, curN);

        framesTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D_ARRAY, framesTex);
        gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

        // Colour LUT texture (256x1). Defaults to white so layers that don't use
        // u_cmap (e.g. isobars) are unaffected; colourise() uploads the real ramp.
        cmapTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
            new Uint8Array([255, 255, 255, 255]));

        // 2D bridge canvas — this is what MapLibre actually samples.
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
        const lut = colormap(cfg);                  // Uint8Array(256*4) | null
        if (!lut) return;
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut);
    };
    const loadTexture = (cfg) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
            if (!gl || !framesTex) return;
            const fh = Math.floor(img.height / curN);     // per-frame height; depth = curN
            gl.bindTexture(gl.TEXTURE_2D_ARRAY, framesTex);
            gl.texImage3D(gl.TEXTURE_2D_ARRAY, 0, gl.RGBA, img.width, fh, curN,
                0, gl.RGBA, gl.UNSIGNED_BYTE, img);
            texReady = true;
        };
        img.onerror = () => {
            console.warn(`[${sectionKey}] data texture not ready: ${dataUrl(cfg)}`);
        };
        img.src = `${dataUrl(cfg)}?t=${Date.now()}`;
    };
    const drawOffscreen = (t) => {
        gl.viewport(0, 0, glCanvas.width, glCanvas.height);
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);   // straight alpha
        gl.useProgram(program);
        gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
        gl.enableVertexAttribArray(aPos);
        gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D_ARRAY, framesTex);
        gl.uniform1i(uFrames, 0);
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(uCmap, 1);
        gl.uniform1f(uTime, t);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    };
    const loop = () => {
        const now = performance.now();                    // shared clock -> all layers in phase
        let t;
        if (curBounce) {
            const phase = (now % (2 * loopMs)) / loopMs;  // 0..2
            t = phase <= 1 ? phase : 2 - phase;               // ping-pong, seamless
        } else {
            t = (now % loopMs) / loopMs;                  // forward, then reset
        }
        if (texReady) {
            drawOffscreen(t);
            out2d.clearRect(0, 0, outCanvas.width, outCanvas.height);
            out2d.drawImage(glCanvas, 0, 0);                  // same-tick blit -> buffer valid
        }
        rafId = requestAnimationFrame(loop);
    };
    // Progress-HUD descriptor for this layer (shared timeline; identical across layers).
    const hudParams = () => ({
        loopMs,
        bounce: curBounce,
        startHour: Math.max(1, Number(curCommon.forecast_hour) || 1),
        stepHours: Math.max(1, parseInt(curAnim.step_hours, 10) || 6),
        frames: curN,
    });
    const mountAnimated = (cfg) => {
        if (map.getSource(A_SRC)) return;
        loopMs = loopSeconds(curAnim) * 1000;
        const res = resolution(cfg); curW = res.w; curH = res.h;
        curResampling = resampling(curAnim);
        curN = frames(curAnim);
        curBounce = bounce(curAnim);
        if (!initGL()) { webglFailed = true; cleanupGL(); mountStatic(cfg); mode = 'static'; return; }
        texReady = false;
        applyCustomUniforms(cfg);
        colourise(cfg);
        loadTexture(cfg);
        map.addSource(A_SRC, { type: 'canvas', canvas: outCanvas, animate: true, coordinates });
        map.addLayer({ id: A_LYR, type: 'raster', source: A_SRC,
            paint: { 'raster-opacity': opacity, 'raster-fade-duration': 0, 'raster-resampling': curResampling } });
        forecastHud.set(sectionKey, hudParams());
        rafId = requestAnimationFrame(loop);
    };
    const refreshAnimated = (cfg) => {
        const res = resolution(cfg);
        if (res.w !== curW || res.h !== curH || frames(curAnim) !== curN) {   // rebuild GL
            unmountAnimated(); mountAnimated(cfg); return;
        }
        loopMs = loopSeconds(curAnim) * 1000;           // live loop-length change
        const rs = resampling(curAnim);
        if (rs !== curResampling) {                     // cheap paint-only change
            curResampling = rs;
            if (map.getLayer(A_LYR)) map.setPaintProperty(A_LYR, 'raster-resampling', rs);
        }
        curBounce = bounce(curAnim);                    // live; loop() reads it each frame
        forecastHud.set(sectionKey, hudParams());       // live: hours/loop length/bounce
        applyCustomUniforms(cfg);
        colourise(cfg);
        loadTexture(cfg);
    };
    const cleanupGL = () => {
        if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
        if (gl) {
            if (framesTex) gl.deleteTexture(framesTex);
            if (cmapTex) gl.deleteTexture(cmapTex);
            if (quadBuf) gl.deleteBuffer(quadBuf);
            if (program) gl.deleteProgram(program);
            gl.getExtension('WEBGL_lose_context')?.loseContext();
        }
        if (outCanvas && outCanvas.parentNode) outCanvas.parentNode.removeChild(outCanvas);
        gl = null; glCanvas = null; outCanvas = null; out2d = null;
        program = null; quadBuf = null; framesTex = null; cmapTex = null; texReady = false; customLocs = {};
    };
    const unmountAnimated = () => {
        forecastHud.clear(sectionKey);
        if (map.getLayer(A_LYR)) map.removeLayer(A_LYR);
        if (map.getSource(A_SRC)) map.removeSource(A_SRC);
        cleanupGL();
    };

    // ---------- dispatch ----------
    const wanted = (cfg) => (isAnimated(cfg) && !webglFailed) ? 'animated' : 'static';
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
        mode = wanted(cfg);
        if (mode === 'animated') mountAnimated(cfg); else mountStatic(cfg);
        onMount(cfg);
    };
    const refresh = (cfg, globals) => {
        curAnim = (globals && globals.animation) || {};
        curCommon = (globals && globals.common) || {};
        const want = wanted(cfg);
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
        globalKeys: ['animation', 'common'],   // watch shared timing + forecast_hour
        mount, refresh, unmount,
        imageUrl: (cfg) => (isAnimated(cfg) && !webglFailed) ? dataUrl(cfg) : staticUrl(cfg),
        refreshMs, syncMs,
    });
}