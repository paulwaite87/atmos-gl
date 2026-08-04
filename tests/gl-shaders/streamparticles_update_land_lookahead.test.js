// Shader-level test for a coastal-overshoot bug in UPDATE_FS (ui/modules/
// _streamparticles_gl.js), found live in candidate #7 (particle-engine consolidation):
// waves' bars travelling ONSHORE visibly persisted past the coastline before resetting,
// while bars travelling OFFSHORE never started inland. Root cause: the land-reset test
// only checked hasData at the particle's OLD position (`pos`, sampled before
// advection), not the NEW position (`npos`) the step is about to commit to -- so a
// particle whose next step crosses onto land commits that on-land position for a full
// frame (drawn there) before the FOLLOWING frame's stale check finally catches it.
// This pre-existed in _particles_gl.js's identical UPDATE_FS pattern too (so it's not
// new to this migration) but only became visible once bars lived long enough
// (streamparticles_agefade_fractions' lifetime fix) to actually reach a coastline
// during their lifetime.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const LAUNCH_ARGS = ["--use-gl=swiftshader", "--enable-webgl2", "--ignore-gpu-blocklist", "--no-sandbox"];

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

// Runs the real UPDATE_FS for a single ocean particle sitting one step away from a
// land boundary, with a strong onshore (toward-land) velocity -- so this frame's step
// would cross onto land.
async function runApproachingLand() {
  const { UPDATE_FS } = extractFromParticlesEngine("ui/modules/_streamparticles_gl.js", ["UPDATE_FS"]);
  const fsSource = UPDATE_FS.replace("#version 300 es\n", "#version 300 es\n#define POS_FLOAT 1\n");

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ fsSource }) => {
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
        const vsSource = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main(){ v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;
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

        // Particle sits just 0.002 tile-units into the ocean side of the coastline
        // (land is x<0.125 on this 8-wide texture) -- MAX_STEP (0.004) caps the
        // per-frame step regardless of configured speed, so it must start this close
        // for even a maximally-clamped westward step to cross the boundary.
        const startPos = new Float32Array([0.127, 0.5, 0.0, 1.0]);
        const posTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, posTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, startPos);

        const ageTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([128, 128, 0, 255]));

        // 8x8: column x=0 is LAND (alpha=0), rest ocean, uniform STRONG WESTWARD
        // (toward land) velocity everywhere in the ocean.
        const W = 8;
        const velData = new Uint8Array(W * W * 4);
        const vmax = 2.5;
        // vx = -2.0 m/s (strong westward), vy = 0. Encoded: ch = (v+vmax)/(2*vmax).
        const rEnc = Math.round(((-2.0 + vmax) / (2 * vmax)) * 255);
        const gEnc = Math.round(((0.0 + vmax) / (2 * vmax)) * 255);
        for (let y = 0; y < W; y++) {
          for (let x = 0; x < W; x++) {
            const i = (y * W + x) * 4;
            if (x === 0) {
              velData[i + 0] = 128; velData[i + 1] = 128; velData[i + 2] = 0; velData[i + 3] = 0; // land
            } else {
              velData[i + 0] = rEnc; velData[i + 1] = gEnc; velData[i + 2] = 0; velData[i + 3] = 255; // ocean, westward
            }
          }
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

        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), vmax);
        // A large speed multiplier so the single-frame step reliably crosses the
        // remaining ~0.105 tile-space gap to the land boundary (x=0.125).
        gl.uniform1f(gl.getUniformLocation(prog, "u_speed"), 40.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_seed"), 7.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_landReset"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_ageStep"), 0.001);
        gl.uniform1f(gl.getUniformLocation(prog, "u_smoothPx"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_minValue"), 0.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmSpeed"), 0.001);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmDrop"), 0.0);
        gl.uniform4f(gl.getUniformLocation(prog, "u_bboxPos"), 0.0, 0.0, 1.0, 1.0);

        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);

        const posOut = new Float32Array(4);
        gl.readBuffer(gl.COLOR_ATTACHMENT0);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, posOut);
        const ageOut = new Float32Array(4);
        gl.readBuffer(gl.COLOR_ATTACHMENT1);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, ageOut);

        return { startPos: [startPos[0], startPos[1]], outPos: [posOut[0], posOut[1]], outAge: ageOut[0] };
      },
      { fsSource }
    );
  } finally {
    await browser.close();
  }
}

async function main() {
  const result = await runApproachingLand();

  // Without the fix, the particle commits to a position at/past the land boundary
  // (x < 0.125) for this frame, since the reset test only checked hasData at the OLD
  // (still-ocean) position. With the fix, it must reset immediately -- landing back at
  // a random position, age reset to ~0 -- rather than ever writing an on-land x.
  assert(
    result.outPos[0] >= 0.125,
    `expected the particle to be reset away from land rather than committing an on-land x, got outPos=${JSON.stringify(result.outPos)} (land is x<0.125)`
  );
  assert(
    result.outAge < 0.05,
    `expected a reset (age -> ~0) since the step would have crossed onto land, got age=${result.outAge}`
  );

  console.log("PASS: streamparticles_update_land_lookahead");
  console.log(`  approaching land from x=${result.startPos[0]}: reset to x=${result.outPos[0].toFixed(4)}, age=${result.outAge.toFixed(3)}`);
}

main().catch((err) => {
  console.error("FAIL: streamparticles_update_land_lookahead");
  console.error(err.message);
  process.exit(1);
});
