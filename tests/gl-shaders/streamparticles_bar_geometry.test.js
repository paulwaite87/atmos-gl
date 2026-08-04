// Shader-level test for the stream particle engine's new 'bar' geometry mode
// (candidate #7, particle-engine consolidation -- see
// docs/adr/0003-keep-waves-on-the-oriented-quad-engine.md's supersede banner).
// Drives the REAL BAR_VS_BODY vertex shader from ui/modules/_streamparticles_gl.js
// via WebGL2 transform feedback (captures gl_Position/v_speed/v_t per vertex without
// rasterizing), so this exercises the exact GLSL that ships to production, not a
// JS re-derivation of the geometry math.
//
// projectTile() is normally supplied by MapLibre's projection prelude at link time;
// here it's replaced with a trivial identity mock (clip.xy = merc*2-1, w=1) so the
// expected screen-space output is exactly computable in JS.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const IDENTITY_PROJECT_TILE = `vec4 projectTile(vec2 p){ return vec4(p * 2.0 - 1.0, 0.0, 1.0); }`;

const LAUNCH_ARGS = [
  "--use-gl=swiftshader",
  "--enable-webgl2",
  "--ignore-gpu-blocklist",
  "--no-sandbox",
];

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

function encodeVel(vx, vy, vmax) {
  const r = Math.round(((vx + vmax) / (2 * vmax)) * 255);
  const g = Math.round(((vy + vmax) / (2 * vmax)) * 255);
  return [r, g, 0, 255];
}

async function runBarQuad({ vx, vy, vmax, halfLen, halfThick, eps, viewport, hasData }) {
  const { BAR_VS_BODY } = extractFromParticlesEngine("ui/modules/_streamparticles_gl.js", ["BAR_VS_BODY"]);
  const vsSource = `#version 300 es\n${IDENTITY_PROJECT_TILE}\n#define POS_FLOAT 1\n${BAR_VS_BODY}`;
  const fsSource = `#version 300 es
precision highp float;
out vec4 fragColor;
void main(){ fragColor = vec4(1.0); }`;

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ vsSource, fsSource, vx, vy, vmax, halfLen, halfThick, eps, viewport, hasData, velRgba }) => {
        const canvas = document.createElement("canvas");
        canvas.width = 1; canvas.height = 1;
        const gl = canvas.getContext("webgl2");
        gl.getExtension("EXT_color_buffer_float");

        function compile(type, src) {
          const sh = gl.createShader(type);
          gl.shaderSource(sh, src);
          gl.compileShader(sh);
          if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
          return sh;
        }
        const prog = gl.createProgram();
        gl.attachShader(prog, compile(gl.VERTEX_SHADER, vsSource));
        gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fsSource));
        gl.transformFeedbackVaryings(prog, ["gl_Position", "v_speed", "v_t"], gl.INTERLEAVED_ATTRIBS);
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));

        // Single particle, head at equator/prime-meridian-ish (0.5, 0.5) in [0,1] tile space.
        const headTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, headTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, new Float32Array([0.5, 0.5, 0, 1]));

        const ageTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([80, 0, 0, 255]));

        // Uniform 4x4 velocity field (all-ocean or all-land per hasData), matching
        // production's velTex sampling params.
        const W = 4;
        const velData = new Uint8Array(W * W * 4);
        for (let i = 0; i < W * W; i++) {
          velData[i * 4 + 0] = velRgba[0];
          velData[i * 4 + 1] = velRgba[1];
          velData[i * 4 + 2] = velRgba[2];
          velData[i * 4 + 3] = hasData ? 255 : 0;
        }
        const velTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, W, W, 0, gl.RGBA, gl.UNSIGNED_BYTE, velData);

        const tfBuf = gl.createBuffer();
        gl.bindBuffer(gl.TRANSFORM_FEEDBACK_BUFFER, tfBuf);
        gl.bufferData(gl.TRANSFORM_FEEDBACK_BUFFER, 6 * 6 * 4, gl.STATIC_DRAW);
        gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER, 0, tfBuf);

        const vao = gl.createVertexArray();   // attributeless -- geometry is gl_VertexID-driven
        gl.bindVertexArray(vao);

        gl.useProgram(prog);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, headTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_head"), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_vel"), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_age"), 2);
        gl.uniform1f(gl.getUniformLocation(prog, "u_res"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), vmax);
        gl.uniform1f(gl.getUniformLocation(prog, "u_smoothPx"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_halfLen"), halfLen);
        gl.uniform1f(gl.getUniformLocation(prog, "u_halfThick"), halfThick);
        gl.uniform1f(gl.getUniformLocation(prog, "u_eps"), eps);
        gl.uniform2f(gl.getUniformLocation(prog, "u_viewport"), viewport[0], viewport[1]);

        gl.enable(gl.RASTERIZER_DISCARD);
        gl.beginTransformFeedback(gl.TRIANGLES);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        gl.endTransformFeedback();
        gl.disable(gl.RASTERIZER_DISCARD);

        const out = new Float32Array(36);
        gl.bindBuffer(gl.TRANSFORM_FEEDBACK_BUFFER, tfBuf);
        gl.getBufferSubData(gl.TRANSFORM_FEEDBACK_BUFFER, 0, out);

        const verts = [];
        for (let i = 0; i < 6; i++) {
          const o = i * 6;
          verts.push({ x: out[o], y: out[o + 1], z: out[o + 2], w: out[o + 3], speed: out[o + 4], t: out[o + 5] });
        }
        return verts;
      },
      { vsSource, fsSource, vx, vy, vmax, halfLen, halfThick, eps, viewport, hasData, velRgba: encodeVel(vx, vy, vmax) }
    );
  } finally {
    await browser.close();
  }
}

async function main() {
  const vmax = 2.5;
  const viewport = [1000, 1000];
  const halfLen = 9.0, halfThick = 2.0, eps = 0.001;

  // Eastward flow at the equator: dirEq is exactly (1,0) in equirect space, so the
  // bar's LENGTH axis (perpendicular to flow, the crest) should run along screen Y,
  // and its THICKNESS axis (along flow) should run along screen X.
  const eastward = await runBarQuad({
    vx: 1.25, vy: 0.0, vmax, halfLen, halfThick, eps, viewport, hasData: true,
  });

  const expectedXs = new Set([-halfThick, halfThick].map((v) => (v * 2) / viewport[0]));
  const expectedYs = new Set([-halfLen, halfLen].map((v) => (v * 2) / viewport[1]));
  const TOL = 1e-3;
  const closeToAny = (value, set) => [...set].some((e) => Math.abs(value - e) < TOL);

  for (const v of eastward) {
    assert(v.w > 0.9, `eastward: expected a valid (non-discarded) vertex, got w=${v.w}`);
    assert(closeToAny(v.x, expectedXs), `eastward: vertex.x=${v.x} not close to expected thickness offsets ${[...expectedXs]}`);
    assert(closeToAny(v.y, expectedYs), `eastward: vertex.y=${v.y} not close to expected length offsets ${[...expectedYs]}`);
  }

  // Fixed length: a much faster eastward flow (same direction) must produce the
  // IDENTICAL quad -- bar geometry does not scale with speed (unlike the streak
  // primitive's lenSpeedScale).
  const eastwardFast = await runBarQuad({
    vx: 2.2, vy: 0.0, vmax, halfLen, halfThick, eps, viewport, hasData: true,
  });
  for (let i = 0; i < 6; i++) {
    assert(
      Math.abs(eastward[i].x - eastwardFast[i].x) < TOL && Math.abs(eastward[i].y - eastwardFast[i].y) < TOL,
      `fixed-length: corner ${i} differs between speeds (${JSON.stringify(eastward[i])} vs ${JSON.stringify(eastwardFast[i])})`
    );
  }
  assert(eastwardFast[0].speed > eastward[0].speed, "fixed-length: v_speed should still track the faster flow even though geometry doesn't");

  // No-data (land): every vertex must be discarded to the off-screen sentinel.
  const noData = await runBarQuad({
    vx: 1.25, vy: 0.0, vmax, halfLen, halfThick, eps, viewport, hasData: false,
  });
  for (const v of noData) {
    assert(v.x >= 1.9 && v.y >= 1.9, `no-data: expected the discard sentinel (2,2,2,1), got ${JSON.stringify(v)}`);
  }

  console.log("PASS: streamparticles_bar_geometry");
  console.log(`  eastward flow: thickness(x) in ${[...expectedXs]}, length(y) in ${[...expectedYs]}`);
  console.log(`  fixed-length confirmed across speeds; no-data correctly discarded`);
}

main().catch((err) => {
  console.error("FAIL: streamparticles_bar_geometry");
  console.error(err.message);
  process.exit(1);
});
