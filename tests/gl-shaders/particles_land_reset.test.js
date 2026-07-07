// Shader-level regression test: runs the REAL, unmodified UPDATE_FS shader from
// ui/modules/_particles_gl.js against a synthetic wave texture with a clean land/ocean
// split, on a real WebGL2 context (headless Chromium + SwiftShader software
// rendering -- no GPU required). Verifies the land-reset behaviour a config bug once
// silently disabled for waves (see ui/modules/waves.js's landReset option): a particle
// that ends up on a no-data (land) texel must be reset to a new position, not left to
// sit there indefinitely.
//
// Not run via vitest -- vitest's Node/jsdom environment has no real WebGL2 context.
// This is a standalone script (see package.json's "test:shaders"), invoked directly
// with Node and asserting via process.exit, so it's a clean CI pass/fail gate.
import { chromium } from "playwright";
import { extractFromParticlesEngine, captureParticleControllerOpts } from "./extract_shaders.js";

const FULLSCREEN_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main(){ v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;

const LAUNCH_ARGS = [
  "--use-gl=swiftshader",
  "--enable-webgl2",
  "--ignore-gpu-blocklist",
  "--no-sandbox",
];

async function runUpdateShaderOnLandParticle(landReset) {
  const { UPDATE_FS } = extractFromParticlesEngine(
    "ui/modules/_particles_gl.js",
    ["UPDATE_FS"]
  );
  // Inject POS_FLOAT the same way the real engine's linkProg() does on the float path
  // (EXT_color_buffer_float available -- true for SwiftShader and for real GPUs alike).
  const fsSource = UPDATE_FS.replace(
    "#version 300 es\n",
    "#version 300 es\n#define POS_FLOAT 1\n"
  );

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ vsSource, fsSource, landReset }) => {
        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        const gl = canvas.getContext("webgl2");
        gl.getExtension("EXT_color_buffer_float");

        function compile(type, src) {
          const sh = gl.createShader(type);
          gl.shaderSource(sh, src);
          gl.compileShader(sh);
          if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
            throw new Error("shader compile failed: " + gl.getShaderInfoLog(sh));
          }
          return sh;
        }
        const prog = gl.createProgram();
        gl.attachShader(prog, compile(gl.VERTEX_SHADER, vsSource));
        gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fsSource));
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
          throw new Error("link failed: " + gl.getProgramInfoLog(prog));
        }

        const quad = new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]);
        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "a_pos");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

        // Single test particle, sitting exactly on a LAND texel center.
        const posTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, posTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        const startPos = new Float32Array([0.0625, 0.5, 0.0, 1.0]); // 8-wide tex, texel 0 centre
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, startPos);

        // Non-zero starting age, so "reset to 0" vs "advanced" is unambiguous.
        const ageTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        const startAge = new Uint8Array([Math.round(0.5 * 255), 128, 0, 255]);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, startAge);

        // 8x8 wind/wave texture: column x=0 is LAND (alpha=0), rest is clean ocean.
        const W = 8;
        const windData = new Uint8Array(W * W * 4);
        for (let y = 0; y < W; y++) {
          for (let x = 0; x < W; x++) {
            const i = (y * W + x) * 4;
            if (x === 0) {
              windData[i + 0] = 128;
              windData[i + 1] = 128;
              windData[i + 2] = 0;
              windData[i + 3] = 0; // land, alpha=0
            } else {
              windData[i + 0] = 140;
              windData[i + 1] = 140;
              windData[i + 2] = 0;
              windData[i + 3] = 255; // ocean, small +velocity
            }
          }
        }
        const windTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, W, W, 0, gl.RGBA, gl.UNSIGNED_BYTE, windData);

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
        if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
          throw new Error("FBO incomplete");
        }
        gl.viewport(0, 0, 1, 1);

        gl.useProgram(prog);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, posTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_particles"), 0);
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_age"), 1);
        gl.activeTexture(gl.TEXTURE2);
        gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_wind"), 2);

        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), 40.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_speed"), 0.05);
        gl.uniform1f(gl.getUniformLocation(prog, "u_seed"), 12.34);
        gl.uniform1f(gl.getUniformLocation(prog, "u_landReset"), landReset);
        gl.uniform1f(gl.getUniformLocation(prog, "u_ageStep"), 0.02);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmSpeed"), 0.001); // negligible calm-reset
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmDrop"), 0.0); // disable calm-reset confound
        gl.uniform4f(gl.getUniformLocation(prog, "u_bboxPos"), 0.0, 0.0, 1.0, 1.0); // whole world

        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);

        const posOut = new Float32Array(4);
        gl.readBuffer(gl.COLOR_ATTACHMENT0);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, posOut);
        const ageOut = new Float32Array(4);
        gl.readBuffer(gl.COLOR_ATTACHMENT1);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, ageOut);

        return {
          startPos: [startPos[0], startPos[1]],
          outPos: [posOut[0], posOut[1]],
          outAge: ageOut[0],
        };
      },
      { vsSource: FULLSCREEN_VS, fsSource, landReset }
    );
  } finally {
    await browser.close();
  }
}

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

async function main() {
  // Ties this test to the REAL production wiring: does ui/modules/waves.js actually
  // set landReset, not a hand-copied literal in this test that could drift from what
  // the file really does.
  const wavesOpts = captureParticleControllerOpts("ui/modules/waves.js");
  assert(
    typeof wavesOpts.landReset === "function",
    "waves.js: expected a landReset option on the createParticleGLController call"
  );
  assert(
    wavesOpts.landReset({}) >= 0.5,
    `waves.js: expected landReset to evaluate >= 0.5 (land-masking enabled), got ${wavesOpts.landReset({})}`
  );

  const off = await runUpdateShaderOnLandParticle(0.0);
  const on = await runUpdateShaderOnLandParticle(1.0);

  assert(
    Math.abs(off.outPos[0] - off.startPos[0]) < 1e-4 &&
      Math.abs(off.outPos[1] - off.startPos[1]) < 1e-4,
    `landReset=0: expected the particle to stay stuck at its land position ${JSON.stringify(off.startPos)}, got ${JSON.stringify(off.outPos)}`
  );
  assert(
    off.outAge > 0.1,
    `landReset=0: expected age to have advanced past its 0.5 start (not reset), got ${off.outAge}`
  );
  assert(
    on.outAge < 0.1,
    `landReset=1: expected the particle to be reset (age -> ~0), got ${on.outAge}`
  );
  assert(
    Math.abs(on.outPos[0] - on.startPos[0]) > 1e-3 ||
      Math.abs(on.outPos[1] - on.startPos[1]) > 1e-3,
    `landReset=1: expected the particle to be moved to a new position, stayed at ${JSON.stringify(on.outPos)}`
  );

  console.log("PASS: particles_land_reset");
  console.log(`  landReset=0 (stuck on land): pos=${JSON.stringify(off.outPos)}, age=${off.outAge.toFixed(3)}`);
  console.log(`  landReset=1 (reset away):    pos=${JSON.stringify(on.outPos)}, age=${on.outAge.toFixed(3)}`);
}

main().catch((err) => {
  console.error("FAIL: particles_land_reset");
  console.error(err.message);
  process.exit(1);
});
