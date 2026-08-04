// Shader-level test for the stream particle engine's calm-cell de-clumping (candidate
// #7, particle-engine consolidation) -- ports _particles_gl.js's "calm-zone handling"
// (see that file's opts docstring on calmSpeed/calmDrop/calmFade) onto
// _streamparticles_gl.js's UPDATE_FS (respawn probability) and trailFragmentShader
// (opacity dimming), against the REAL, unmodified shader source from that file.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const FULLSCREEN_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main(){ v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;

const LAUNCH_ARGS = ["--use-gl=swiftshader", "--enable-webgl2", "--ignore-gpu-blocklist", "--no-sandbox"];

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

function compileHelper() {
  return function compile(gl, type, src) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
    return sh;
  };
}

// Runs the real UPDATE_FS against a single slow-moving ocean particle (speed well
// below u_calmSpeed), varying only u_calmDrop, and reports whether it respawned.
async function runUpdateCalmCase(calmDrop) {
  const { UPDATE_FS } = extractFromParticlesEngine("ui/modules/_streamparticles_gl.js", ["UPDATE_FS"]);
  const fsSource = UPDATE_FS.replace("#version 300 es\n", "#version 300 es\n#define POS_FLOAT 1\n");

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ fsSource, calmDrop, vsSource }) => {
        const canvas = document.createElement("canvas");
        canvas.width = 1; canvas.height = 1;
        const gl = canvas.getContext("webgl2");
        gl.getExtension("EXT_color_buffer_float");

        function compile(type, src) {
          const sh = gl.createShader(type);
          gl.shaderSource(sh, src);
          gl.compileShader(sh);
          if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error("compile: " + gl.getShaderInfoLog(sh));
          return sh;
        }
        const prog = gl.createProgram();
        gl.attachShader(prog, compile(gl.VERTEX_SHADER, vsSource));
        gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fsSource));
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error("link: " + gl.getProgramInfoLog(prog));

        const quad = new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]);
        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "a_pos");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

        const startPos = new Float32Array([0.5, 0.5, 0.0, 1.0]);
        const posTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, posTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, startPos);

        // Non-zero starting age so "reset to 0" vs "advanced" is unambiguous.
        const ageTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([128, 128, 0, 255]));

        // Uniform, all-ocean, near-ZERO velocity field -- a genuinely calm cell.
        const W = 4;
        const velData = new Uint8Array(W * W * 4);
        for (let i = 0; i < W * W; i++) {
          velData[i * 4 + 0] = 128; velData[i * 4 + 1] = 128; velData[i * 4 + 2] = 0; velData[i * 4 + 3] = 255;
        }
        const velTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, W, W, 0, gl.RGBA, gl.UNSIGNED_BYTE, velData);

        const outPosTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, outPosTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, null);
        const outAgeTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, outAgeTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, null);

        const fbo = gl.createFramebuffer();
        gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, outPosTex, 0);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, outAgeTex, 0);
        gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
        if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error("FBO incomplete");
        gl.viewport(0, 0, 1, 1);

        gl.useProgram(prog);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, posTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_particles"), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_age"), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_vel"), 2);

        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), 2.5);
        gl.uniform1f(gl.getUniformLocation(prog, "u_speed"), 0.4);
        gl.uniform1f(gl.getUniformLocation(prog, "u_seed"), 42.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_landReset"), 0.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_ageStep"), 0.005);
        gl.uniform1f(gl.getUniformLocation(prog, "u_smoothPx"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmSpeed"), 0.05);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmDrop"), calmDrop);
        gl.uniform4f(gl.getUniformLocation(prog, "u_bboxPos"), 0.0, 0.0, 1.0, 1.0);

        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);

        const posOut = new Float32Array(4);
        gl.readBuffer(gl.COLOR_ATTACHMENT0);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, posOut);
        const ageOut = new Float32Array(4);
        gl.readBuffer(gl.COLOR_ATTACHMENT1);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, ageOut);
        return { outPos: [posOut[0], posOut[1]], outAge: ageOut[0] };
      },
      { fsSource, calmDrop, vsSource: FULLSCREEN_VS }
    );
  } finally {
    await browser.close();
  }
}

// Runs the real trailFragmentShader(tailFadeEnd) against fixed v_speed/v_t/v_age
// (fed via a tiny pass-through VS), reading back the resulting alpha.
async function runFragmentCalmFade({ calmFade, speedFrac }) {
  const { trailFragmentShader } = extractFromParticlesEngine("ui/modules/_streamparticles_gl.js", ["trailFragmentShader"]);
  const fsSource = trailFragmentShader(0.35);

  const vsSource = `#version 300 es
in vec2 a_pos;
out float v_speed;
out float v_t;
out float v_age;
uniform float u_testSpeed, u_testT, u_testAge;
void main(){
    v_speed = u_testSpeed; v_t = u_testT; v_age = u_testAge;
    gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0);
}`;

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ vsSource, fsSource, calmFade, speedFrac }) => {
        const canvas = document.createElement("canvas");
        canvas.width = 1; canvas.height = 1;
        const gl = canvas.getContext("webgl2");
        gl.getExtension("EXT_color_buffer_float");

        function compile(type, src) {
          const sh = gl.createShader(type);
          gl.shaderSource(sh, src);
          gl.compileShader(sh);
          if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error("compile: " + gl.getShaderInfoLog(sh));
          return sh;
        }
        const prog = gl.createProgram();
        gl.attachShader(prog, compile(gl.VERTEX_SHADER, vsSource));
        gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fsSource));
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error("link: " + gl.getProgramInfoLog(prog));

        const quad = new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]);
        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "a_pos");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

        const cmapTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([255, 255, 255, 255]));

        const outTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, outTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, null);
        const fbo = gl.createFramebuffer();
        gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, outTex, 0);
        gl.viewport(0, 0, 1, 1);

        gl.useProgram(prog);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_cmap"), 0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), 2.5);
        gl.uniform1f(gl.getUniformLocation(prog, "u_maxspeed"), 2.5);
        gl.uniform1f(gl.getUniformLocation(prog, "u_alpha"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmFade"), calmFade);
        gl.uniform1f(gl.getUniformLocation(prog, "u_testSpeed"), speedFrac * 2.5);
        gl.uniform1f(gl.getUniformLocation(prog, "u_testT"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_testAge"), 0.5);

        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ZERO);
        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);

        const out = new Float32Array(4);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, out);
        return { alpha: out[3] };
      },
      { vsSource, fsSource, calmFade, speedFrac }
    );
  } finally {
    await browser.close();
  }
}

async function main() {
  // UPDATE_FS: a slow (near-zero-velocity) particle with u_calmDrop=1.0 must ALWAYS
  // respawn (deterministic: calmDrop formula evaluates to exactly 1.0 when speed<<calmSpeed,
  // and rand() e [0,1) is always < 1.0).
  const alwaysCalm = await runUpdateCalmCase(1.0);
  assert(alwaysCalm.outAge < 0.05, `u_calmDrop=1.0: expected the calm particle to respawn (age->~0), got age=${alwaysCalm.outAge}`);

  // u_calmDrop=0.0 must NEVER trigger a calm-reset -- the same slow particle should
  // just advect normally (age advances, not reset) since it's not near land/domain edge.
  const neverCalm = await runUpdateCalmCase(0.0);
  assert(neverCalm.outAge > 0.4, `u_calmDrop=0.0: expected the particle to advect normally (age advances), got age=${neverCalm.outAge}`);

  // trailFragmentShader: at low speed fraction, calmFade=0.6 should dim alpha relative
  // to calmFade=0.
  const dimmed = await runFragmentCalmFade({ calmFade: 0.6, speedFrac: 0.0 });
  const undimmed = await runFragmentCalmFade({ calmFade: 0.0, speedFrac: 0.0 });
  assert(dimmed.alpha < undimmed.alpha, `calmFade=0.6 should dim low-speed alpha below calmFade=0 (got ${dimmed.alpha} vs ${undimmed.alpha})`);

  console.log("PASS: streamparticles_calm_cell");
  console.log(`  calmDrop=1.0 -> age=${alwaysCalm.outAge.toFixed(3)} (respawned); calmDrop=0.0 -> age=${neverCalm.outAge.toFixed(3)} (advected)`);
  console.log(`  calmFade dimming: calmFade=0 alpha=${undimmed.alpha.toFixed(3)}, calmFade=0.6 alpha=${dimmed.alpha.toFixed(3)}`);
}

main().catch((err) => {
  console.error("FAIL: streamparticles_calm_cell");
  console.error(err.message);
  process.exit(1);
});
