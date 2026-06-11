import { liveLayerSync } from './_refresh.js';

// Full-world Web-Mercator corners (matches the raster layers). MapLibre projects the
// offscreen mercator canvas onto the globe exactly as it does for image/canvas sources.
const MERCATOR_CORNERS = [
    [-180, 85.051129], [180, 85.051129], [180, -85.051129], [-180, -85.051129],
];

// level_of_detail ("1"/"2"/"3" = Low/Medium/High) bundles the perf/quality knobs:
// canvas resolution (sharpness) and particle count (density). Medium is the default.
const lodOf = (cfg) => { const n = parseInt(cfg.level_of_detail, 10); return (n === 1 || n === 3) ? n : 2; };
const LOD_RES = { 1: 2048, 2: 4096, 3: 8192 };
const LOD_COUNT = { 1: 25000, 2: 65000, 3: 160000 };

// ---- shaders -------------------------------------------------------------

const QUAD_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() { v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;

// Position packing: a coordinate in [0,1] -> 16 bits across two 8-bit channels.
// Lets the whole particle state live in a plain RGBA8 texture (no float-texture ext).
const PACK = `
vec2 packPos(float x){ float e = floor(clamp(x,0.0,1.0)*65535.0 + 0.5);
    float hi = floor(e/256.0); return vec2(hi, e - hi*256.0)/255.0; }
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
vec4 encodePos(vec2 p){ return vec4(packPos(p.x), packPos(p.y)); }
float rand(vec2 co){ return fract(sin(dot(co, vec2(12.9898,78.233))) * 43758.5453); }`;

// Advection: step each particle along the wind, wrap longitude, randomly respawn
// (more often where the wind is slow, so calm regions don't fill with stuck dots).
const UPDATE_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_particles;
uniform sampler2D u_wind;
uniform float u_vmax, u_speed, u_dropRate, u_dropBump, u_dropSpeed, u_seed;
const float PI = 3.141592653589793;
// Scales an m/s velocity into a per-frame step in normalised [0,1] space. Picked so
// the default u_speed gives a few pixels/frame; u_speed is the user-facing multiplier.
const float STEP = 0.0005;
${PACK}
void main(){
    vec2 pos = decodePos(texture(u_particles, v_uv));
    float lat = (0.5 - pos.y) * PI;
    vec4 w = texture(u_wind, pos);
    vec2 vel = (w.a < 0.5) ? vec2(0.0) : (w.rg * (2.0*u_vmax) - u_vmax);   // m/s (east,north)
    float coslat = max(cos(lat), 0.05);
    // east step is half of north per equirect aspect (x spans 360 deg, y spans 180);
    // /coslat keeps zonal motion right as meridians converge.
    vec2 d = vec2(vel.x / coslat * 0.5, -vel.y) * (u_speed * STEP);
    vec2 npos = pos + d;
    npos.x = fract(npos.x + 1.0);                       // wrap longitude seam
    float speed = length(vel);
    float drop = u_dropRate + (1.0 - clamp(speed/u_dropSpeed, 0.0, 1.0)) * u_dropBump;
    vec2 seed = (pos + v_uv) * (u_seed + 1.0);
    vec2 randPos = vec2(rand(seed + 1.3), rand(seed + 2.7));
    bool reset = (rand(seed) < drop) || (npos.y <= 0.0) || (npos.y >= 1.0);
    fragColor = encodePos(reset ? randPos : npos);
}`;

// Each particle -> a point placed in mercator clip space (so the canvas content lines
// up with the mercator source corners); coloured by wind speed via the LUT.
const DRAW_VS = `#version 300 es
precision highp float;
in float a_index;
uniform sampler2D u_particles;
uniform sampler2D u_wind;
uniform float u_res, u_vmax, u_pointSize;
out float v_speed;
const float PI = 3.141592653589793;
const float LAT_MAX = 1.4844222297453324;   // 85.0511 deg
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
void main(){
    float col = mod(a_index, u_res);
    float row = floor(a_index / u_res);
    vec4 s = texelFetch(u_particles, ivec2(int(col), int(row)), 0);
    vec2 pos = decodePos(s);
    vec4 w = texture(u_wind, pos);
    vec2 vel = (w.a < 0.5) ? vec2(0.0) : (w.rg * (2.0*u_vmax) - u_vmax);
    v_speed = length(vel);
    float lat = clamp((0.5 - pos.y) * PI, -LAT_MAX, LAT_MAX);
    float mercY = log(tan(PI*0.25 + lat*0.5));          // mercator northing (radians)
    float y01 = 0.5 - mercY / (2.0 * PI);               // 0 at north edge
    gl_Position = vec4(pos.x*2.0 - 1.0, 1.0 - 2.0*y01, 0.0, 1.0);
    gl_PointSize = u_pointSize;
}`;

const DRAW_FS = `#version 300 es
precision highp float;
in float v_speed;
out vec4 fragColor;
uniform sampler2D u_cmap;
uniform float u_maxspeed, u_alpha;
void main(){
    float t = clamp(v_speed / u_maxspeed, 0.0, 1.0);
    fragColor = vec4(texture(u_cmap, vec2(t, 0.5)).rgb, u_alpha);
}`;


// Waves mode: each particle -> a short bar drawn PERPENDICULAR to the swell direction
// (6 verts/particle via gl_VertexID, no attributes). Mercator is conformal, so the
// swell direction in clip space is just normalize(vel); we rotate it 90deg, correct
// for canvas aspect to keep a constant pixel length, and lay a thin oriented quad.
const BAR_VS = `#version 300 es
precision highp float;
uniform sampler2D u_particles;
uniform sampler2D u_wind;
uniform float u_res, u_vmax, u_W, u_H, u_halfLen, u_halfThick;
out float v_speed;
const float PI = 3.141592653589793;
const float LAT_MAX = 1.4844222297453324;
float unpackPos(vec2 c){ return (c.x*255.0*256.0 + c.y*255.0)/65535.0; }
vec2 decodePos(vec4 c){ return vec2(unpackPos(c.rg), unpackPos(c.ba)); }
void main(){
    int pid = gl_VertexID / 6;
    int corner = gl_VertexID - pid*6;
    float col = mod(float(pid), u_res);
    float row = floor(float(pid) / u_res);
    vec4 s = texelFetch(u_particles, ivec2(int(col), int(row)), 0);
    vec2 pos = decodePos(s);
    vec4 w = texture(u_wind, pos);
    vec2 vel = (w.a < 0.5) ? vec2(0.0) : (w.rg * (2.0*u_vmax) - u_vmax);
    v_speed = length(vel);

    float lat = clamp((0.5 - pos.y) * PI, -LAT_MAX, LAT_MAX);
    float mercY = log(tan(PI*0.25 + lat*0.5));
    float y01 = 0.5 - mercY / (2.0 * PI);
    vec2 center = vec2(pos.x*2.0 - 1.0, 1.0 - 2.0*y01);

    vec2 dirClip = (v_speed > 1e-4) ? normalize(vel) : vec2(0.0, 1.0);
    vec2 dirPix = normalize(vec2(dirClip.x * u_W, dirClip.y * u_H));   // -> pixel space
    vec2 perpPix = vec2(-dirPix.y, dirPix.x);                          // 90deg = bar long axis
    vec2 ab[6] = vec2[6](vec2(-1.0,-1.0), vec2(1.0,-1.0), vec2(-1.0,1.0),
                         vec2(-1.0, 1.0), vec2(1.0,-1.0), vec2( 1.0,1.0));
    vec2 c = ab[corner];
    vec2 offPix = perpPix * (c.x * u_halfLen) + dirPix * (c.y * u_halfThick);
    vec2 offClip = vec2(offPix.x * 2.0 / u_W, offPix.y * 2.0 / u_H);
    gl_Position = vec4(center + offClip, 0.0, 1.0);
}`;

// Textured fullscreen quad with an opacity multiply (used to fade the trail buffer
// and to blit the trail buffer to the visible canvas).
const SCREEN_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_opacity;
void main(){ fragColor = texture(u_tex, v_uv) * u_opacity; }`;

// ---- helper --------------------------------------------------------------

export function createParticleController(map, opts) {
    const {
        sectionKey = 'wind',
        initialConfig,
        coordinates = MERCATOR_CORNERS,
        vmax = 40.0,                                    // must match backend VMAX_WIND
        colormap = null,                                // (cfg) -> Uint8Array(256*4)
        refreshMs, syncMs,
        staticUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile}`,
        dataUrl = (cfg) => `${window.MAP_UI}/${cfg.outfile.replace(/\.png$/, '_data.png')}`,
        // tunables (resolvers over the layer config)
        // resolution + particle count come from level_of_detail; an explicit
        // particle_resolution / particle_count key still overrides (power-user escape hatch).
        resolution = (cfg) => {
            const explicit = parseInt(cfg.particle_resolution, 10);
            const w = Math.max(1024, explicit > 0 ? explicit : (LOD_RES[lodOf(cfg)] || 4096));
            return { w, h: Math.round(w / 2) };
        },
        particleCount = (cfg) => {
            const explicit = parseInt(cfg.particle_count, 10);
            return Math.max(256, explicit > 0 ? explicit : (LOD_COUNT[lodOf(cfg)] || 65000));
        },
        // particle_speed is a friendly 0-100 "speed" (Slow..Fast). Mapped to the internal
        // advection multiplier and clamped, so negatives / over-range can't produce oddities.
        speed = (cfg) => { const p = Number(cfg.particle_speed);
                           return (isFinite(p) ? Math.min(100, Math.max(0, p)) : 50) / 200; },
        // trail_fade: 0-100 "trail length" -> internal exponential fade (0.85 short .. 0.99 long).
        fade = (cfg) => { const v = Number(cfg.trail_fade);
                          const c = isFinite(v) ? Math.min(100, Math.max(0, v)) : 80;
                          return 0.85 + (c / 100) * 0.14; },
        // particle_size: 1-5 line thickness in px.
        pointSize = (cfg) => { const v = Number(cfg.particle_size);
                               return isFinite(v) ? Math.min(5, Math.max(0.5, v)) : 1.5; },
        dropRate = (cfg) => (cfg.drop_rate != null ? Number(cfg.drop_rate) : 0.003),
        dropBump = (cfg) => (cfg.drop_rate_bump != null ? Number(cfg.drop_rate_bump) : 0.012),
        maxSpeedColor = (cfg) => (Number(cfg.max_speed_color) > 0 ? Number(cfg.max_speed_color) : 30.0),
        // particle_alpha: 0-100 opacity.
        alpha = (cfg) => { const v = Number(cfg.particle_alpha);
                           const c = isFinite(v) ? Math.min(100, Math.max(0, v)) : 90;
                           return c / 100; },
        // 'streaks' (wind: GL points + fading trail) or 'bars' (waves: oriented quads
        // drawn perpendicular to the swell direction, no trail — the windy.com look).
        drawMode = 'streaks',
        // wind falls back to a static barbs PNG when not animated / no WebGL; waves has
        // no such PNG (the heat tiles are its base), so it passes false -> 'none'.
        staticFallback = true,
        // bar_length: half-length of a swell bar in px (1-20), along the perpendicular.
        barLength = (cfg) => { const v = Number(cfg.bar_length);
                               return isFinite(v) ? Math.min(20, Math.max(1, v)) : 7; },
        // fired alongside the animated (particle) layer only — used for the speed legend
        onMount = () => {}, onRefresh = () => {}, onUnmount = () => {},
    } = opts;

    const S_SRC = `${sectionKey}-source`, S_LYR = `${sectionKey}-layer`;
    const A_SRC = `${sectionKey}-anim-source`, A_LYR = `${sectionKey}-anim-layer`;
    const isAnimated = (cfg) => !!cfg.animated;

    let mode = null, webglFailed = false;
    let gl = null, glCanvas = null, outCanvas = null, out2d = null, rafId = null;
    let W = 2048, H = 1024, RES = 256, count = 65536;
    let windReady = false;

    // GL objects
    let updateProg = null, drawProg = null, screenProg = null, barProg = null;
    let quadBuf = null, indexBuf = null;
    let windTex = null, cmapTex = null;
    let stateTex = [null, null], stateFbo = [null, null], stateCur = 0;
    let screenTex = [null, null], screenFbo = [null, null], screenCur = 0;

    // live params (read each frame / draw)
    let curSpeed = 0.25, curFade = 0.96, curPoint = 1.5, curDropRate = 0.003,
        curDropBump = 0.012, curMaxSpeed = 30.0, curAlpha = 0.9, curSubSteps = 1, curBarLen = 7.0;

    // ---- static (barbs PNG) fallback ----
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

    // ---- GL plumbing ----
    const compile = (type, src) => {
        const sh = gl.createShader(type);
        gl.shaderSource(sh, src); gl.compileShader(sh);
        if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
            console.error(`[${sectionKey}] shader compile:`, gl.getShaderInfoLog(sh));
            return null;
        }
        return sh;
    };
    const program = (vs, fs) => {
        const v = compile(gl.VERTEX_SHADER, vs), f = compile(gl.FRAGMENT_SHADER, fs);
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
    const makeTex = (w, h, data, filter) => {
        const t = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
        return t;
    };
    const makeFbo = (tex) => {
        const f = gl.createFramebuffer();
        gl.bindFramebuffer(gl.FRAMEBUFFER, f);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
        return f;
    };
    const randomState = () => {
        const data = new Uint8Array(RES * RES * 4);
        for (let i = 0; i < data.length; i++) data[i] = Math.floor(Math.random() * 256);
        return data;
    };
    const buildState = () => {
        for (let i = 0; i < 2; i++) {
            if (stateTex[i]) gl.deleteTexture(stateTex[i]);
            if (stateFbo[i]) gl.deleteFramebuffer(stateFbo[i]);
            stateTex[i] = makeTex(RES, RES, randomState(), gl.NEAREST);
            stateFbo[i] = makeFbo(stateTex[i]);
        }
        stateCur = 0;
        const idx = new Float32Array(count);
        for (let i = 0; i < count; i++) idx[i] = i;
        if (indexBuf) gl.deleteBuffer(indexBuf);
        indexBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, indexBuf);
        gl.bufferData(gl.ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    };

    const initGL = () => {
        glCanvas = document.createElement('canvas');
        glCanvas.width = W; glCanvas.height = H;
        gl = glCanvas.getContext('webgl2', { premultipliedAlpha: false, antialias: false, preserveDrawingBuffer: true });
        if (!gl) return false;

        updateProg = program(QUAD_VS, UPDATE_FS);
        drawProg = program(DRAW_VS, DRAW_FS);
        screenProg = program(QUAD_VS, SCREEN_FS);
        if (drawMode === 'bars') { barProg = program(BAR_VS, DRAW_FS); if (!barProg) return false; }
        if (!updateProg || !drawProg || !screenProg) return false;

        quadBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]), gl.STATIC_DRAW);

        cmapTex = makeTex(1, 1, new Uint8Array([255, 255, 255, 255]), gl.LINEAR);

        // empty (transparent) trail buffers
        const blank = new Uint8Array(W * H * 4);
        for (let i = 0; i < 2; i++) {
            screenTex[i] = makeTex(W, H, blank, gl.NEAREST);
            screenFbo[i] = makeFbo(screenTex[i]);
        }
        screenCur = 0;
        buildState();
        return true;
    };

    const uploadColormap = (cfg) => {
        if (!gl || !cmapTex || !colormap) return;
        const lut = colormap(cfg);
        if (!lut) return;
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut);
    };

    const loadWind = (cfg) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
            if (!gl) return;
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
        img.onerror = () => console.warn(`[${sectionKey}] wind texture not ready: ${dataUrl(cfg)}`);
        img.src = `${dataUrl(cfg)}?t=${Date.now()}`;
    };

    const bindAttrib = (prog, name, buf, size) => {
        const loc = gl.getAttribLocation(prog, name);
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
    };
    const drawTexture = (tex, opacity) => {
        gl.useProgram(screenProg);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.uniform1i(gl.getUniformLocation(screenProg, 'u_tex'), 0);
        gl.uniform1f(gl.getUniformLocation(screenProg, 'u_opacity'), opacity);
        bindAttrib(screenProg, 'a_pos', quadBuf, 2);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
    };
    const updateParticles = () => {
        gl.bindFramebuffer(gl.FRAMEBUFFER, stateFbo[1 - stateCur]);
        gl.viewport(0, 0, RES, RES);
        gl.disable(gl.BLEND);
        gl.useProgram(updateProg);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, stateTex[stateCur]);
        gl.uniform1i(gl.getUniformLocation(updateProg, 'u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.uniform1i(gl.getUniformLocation(updateProg, 'u_wind'), 1);
        gl.uniform1f(gl.getUniformLocation(updateProg, 'u_vmax'), vmax);
        gl.uniform1f(gl.getUniformLocation(updateProg, 'u_speed'), curSpeed * (2048.0 / W) / curSubSteps);
        gl.uniform1f(gl.getUniformLocation(updateProg, 'u_dropRate'), curDropRate / curSubSteps);
        gl.uniform1f(gl.getUniformLocation(updateProg, 'u_dropBump'), curDropBump / curSubSteps);
        gl.uniform1f(gl.getUniformLocation(updateProg, 'u_dropSpeed'), 10.0);
        gl.uniform1f(gl.getUniformLocation(updateProg, 'u_seed'), Math.random());
        bindAttrib(updateProg, 'a_pos', quadBuf, 2);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        stateCur = 1 - stateCur;                        // new positions now in stateTex[stateCur]
    };
    const drawParticles = () => {
        gl.useProgram(drawProg);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, stateTex[stateCur]);
        gl.uniform1i(gl.getUniformLocation(drawProg, 'u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.uniform1i(gl.getUniformLocation(drawProg, 'u_wind'), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(gl.getUniformLocation(drawProg, 'u_cmap'), 2);
        gl.uniform1f(gl.getUniformLocation(drawProg, 'u_res'), RES);
        gl.uniform1f(gl.getUniformLocation(drawProg, 'u_vmax'), vmax);
        gl.uniform1f(gl.getUniformLocation(drawProg, 'u_pointSize'), curPoint);
        gl.uniform1f(gl.getUniformLocation(drawProg, 'u_maxspeed'), curMaxSpeed);
        gl.uniform1f(gl.getUniformLocation(drawProg, 'u_alpha'), curAlpha);
        bindAttrib(drawProg, 'a_index', indexBuf, 1);
        gl.drawArrays(gl.POINTS, 0, count);
    };


    const drawBars = () => {
        gl.useProgram(barProg);
        const u = (n) => gl.getUniformLocation(barProg, n);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, stateTex[stateCur]);
        gl.uniform1i(u('u_particles'), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.uniform1i(u('u_wind'), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(u('u_cmap'), 2);
        gl.uniform1f(u('u_res'), RES);
        gl.uniform1f(u('u_vmax'), vmax);
        gl.uniform1f(u('u_W'), W);
        gl.uniform1f(u('u_H'), H);
        gl.uniform1f(u('u_halfLen'), curBarLen);
        gl.uniform1f(u('u_halfThick'), Math.max(0.5, curPoint));
        gl.uniform1f(u('u_maxspeed'), curMaxSpeed);
        gl.uniform1f(u('u_alpha'), curAlpha);
        gl.drawArrays(gl.TRIANGLES, 0, count * 6);          // 2 tris/particle, no attribs
    };

    const frame = () => {
        if (gl && windReady && drawMode === 'bars') {
            // Bars: no trail. Advect one step, clear, draw oriented bars, blit.
            updateParticles();
            gl.bindFramebuffer(gl.FRAMEBUFFER, null);
            gl.viewport(0, 0, W, H);
            gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
            gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
            drawBars();
            out2d.clearRect(0, 0, W, H); out2d.drawImage(glCanvas, 0, 0);
            rafId = requestAnimationFrame(frame);
            return;
        }
        if (gl && windReady) {
            // Fade the previous trail buffer once per frame.
            gl.bindFramebuffer(gl.FRAMEBUFFER, screenFbo[1 - screenCur]);
            gl.viewport(0, 0, W, H);
            gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
            gl.disable(gl.BLEND);
            drawTexture(screenTex[screenCur], curFade);

            // Then advance in small sub-steps, dropping a dot each time, so the trail
            // stays continuous (sharp) no matter how fast the flow is moving.
            for (let i = 0; i < curSubSteps; i++) {
                updateParticles();                              // -> state FBO, RES viewport
                gl.bindFramebuffer(gl.FRAMEBUFFER, screenFbo[1 - screenCur]);
                gl.viewport(0, 0, W, H);
                gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
                drawParticles();
            }
            screenCur = 1 - screenCur;

            gl.bindFramebuffer(gl.FRAMEBUFFER, null);
            gl.viewport(0, 0, W, H);
            gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
            gl.disable(gl.BLEND);
            drawTexture(screenTex[screenCur], 1.0);

            out2d.clearRect(0, 0, W, H);
            out2d.drawImage(glCanvas, 0, 0);
        }
        rafId = requestAnimationFrame(frame);
    };

    const applyParams = (cfg) => {
        curSpeed = speed(cfg); curFade = fade(cfg); curPoint = pointSize(cfg);
        curDropRate = dropRate(cfg); curDropBump = dropBump(cfg);
        curMaxSpeed = maxSpeedColor(cfg); curAlpha = alpha(cfg);
        // Lay down dots ~1 point-size apart per frame whatever the speed, so trail
        // sharpness is decoupled from speed. 20.48 = vmax(40 m/s) * 0.512 px-per-(m/s).
        curSubSteps = Math.min(4, Math.max(1, Math.ceil(20.48 * curSpeed / Math.max(0.5, curPoint))));
        curBarLen = barLength(cfg);
        if (drawMode === 'bars') curSubSteps = 1;           // bars don't trail
    };

    const cleanupGL = () => {
        if (rafId) cancelAnimationFrame(rafId);
        rafId = null;
        if (gl) {
            [windTex, cmapTex, ...stateTex, ...screenTex].forEach(t => t && gl.deleteTexture(t));
            [...stateFbo, ...screenFbo].forEach(f => f && gl.deleteFramebuffer(f));
            [quadBuf, indexBuf].forEach(b => b && gl.deleteBuffer(b));
            [updateProg, drawProg, screenProg, barProg].forEach(p => p && gl.deleteProgram(p));
            gl.getExtension('WEBGL_lose_context')?.loseContext();
        }
        if (outCanvas && outCanvas.parentNode) outCanvas.parentNode.removeChild(outCanvas);
        gl = null; glCanvas = null; outCanvas = null; out2d = null; windReady = false;
        windTex = cmapTex = quadBuf = indexBuf = null;
        updateProg = drawProg = screenProg = barProg = null;
        stateTex = [null, null]; stateFbo = [null, null];
        screenTex = [null, null]; screenFbo = [null, null];
    };

    // ---- animated (particles) ----
    const mountAnimated = (cfg) => {
        if (map.getSource(A_SRC)) return;
        const res = resolution(cfg); W = res.w; H = res.h;
        count = particleCount(cfg); RES = Math.max(16, Math.round(Math.sqrt(count))); count = RES * RES;
        applyParams(cfg);

        if (!initGL()) {
            webglFailed = true; cleanupGL();
            if (staticFallback) { mountStatic(cfg); mode = 'static'; } else { mode = 'none'; }
            return;
        }

        outCanvas = document.createElement('canvas');
        outCanvas.width = W; outCanvas.height = H;
        outCanvas.style.position = 'absolute';
        outCanvas.style.left = '-10000px'; outCanvas.style.top = '0px';
        document.body.appendChild(outCanvas);
        out2d = outCanvas.getContext('2d');

        uploadColormap(cfg);
        loadWind(cfg);
        map.addSource(A_SRC, { type: 'canvas', canvas: outCanvas, animate: true, coordinates });
        map.addLayer({ id: A_LYR, type: 'raster', source: A_SRC, paint: { 'raster-opacity': 1.0, 'raster-fade-duration': 0 } });
        frame();
        onMount(cfg);
    };
    const refreshAnimated = (cfg) => {
        const res = resolution(cfg);
        const wantCount = (() => { const r = Math.max(16, Math.round(Math.sqrt(particleCount(cfg)))); return r * r; })();
        if (res.w !== W || res.h !== H || wantCount !== count) {   // structural change -> rebuild
            unmountAnimated(); mountAnimated(cfg); return;
        }
        applyParams(cfg);
        uploadColormap(cfg);
        loadWind(cfg);              // pick up newly regenerated wind data
        onRefresh(cfg);
    };
    const unmountAnimated = () => {
        if (map.getLayer(A_LYR)) map.removeLayer(A_LYR);
        if (map.getSource(A_SRC)) map.removeSource(A_SRC);
        cleanupGL();
        onUnmount();
    };

    // ---- dispatch ----
    const wanted = (cfg) => (isAnimated(cfg) && !webglFailed) ? 'animated' : (staticFallback ? 'static' : 'none');
    const switchTo = (want, cfg) => {
        if (want === 'animated') { unmountStatic(); mountAnimated(cfg); }
        else if (want === 'static') { unmountAnimated(); mountStatic(cfg); }
        else { unmountAnimated(); unmountStatic(); }       // 'none'
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
            ? dataUrl(cfg) : staticUrl(cfg),
    };
}

// Backwards-compatible wrapper (wind): build the controller and drive it from the
// shared liveLayerSync. Layers that need to compose particles with other sources
// (e.g. waves = heat tiles + bars) call createParticleController and drive it from
// their own liveLayerSync instead.
export function createParticleWindLayer(map, opts) {
    const c = createParticleController(map, opts);
    liveLayerSync(map, {
        sectionKey: opts.sectionKey ?? 'wind',
        initialConfig: opts.initialConfig,
        mount: c.mount, refresh: c.refresh, unmount: c.unmount,
        imageUrl: c.imageUrl,
        refreshMs: opts.refreshMs, syncMs: opts.syncMs,
    });
}