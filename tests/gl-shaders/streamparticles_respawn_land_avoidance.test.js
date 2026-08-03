// Shader-level test for a real bug found live in candidate #7 (particle-engine
// consolidation): UPDATE_FS's respawn destination (randPos) was a pure uniform random
// draw within the view bbox, with NO land-avoidance check at all -- so on a bbox with
// significant land coverage, particles could respawn directly ON land (observed live:
// waves' bars "spawning over land"), independent of any coastline-mask-resolution
// mismatch. Fixed by retrying the random draw when u_landReset is on, checking each
// candidate via the cheap validAt() exact-texel check, falling back to the last
// candidate if none validate within a 32-attempt budget.
//
// Two scenarios, both run as 200 simultaneous particles in one draw call (a wide output
// framebuffer; each pixel's distinct v_uv gives each particle its own deterministic
// random stream from the SAME u_seed, matching real per-frame usage), all forced to
// reset this frame (age>=1):
//   - mostly ocean (land = 1/8 of the bbox): the easy case, already covered before this
//     revision. A single draw lands on land ~12.5% of the time.
//   - mostly land (land = 7/8 of the bbox, a narrow ocean strip): the regime actually
//     found broken live -- zoomed into a coastline, the visible bbox can be mostly land
//     with only a narrow ocean strip. A single draw lands on land ~87.5% of the time,
//     so a SMALL retry budget (the original fix's 6 extra attempts) still fails a
//     meaningful fraction of the time (0.875^7 ~ 38%); the 32-attempt budget against the
//     cheap validAt() check reduces that to ~1.4% (0.875^32), reliably 0/200 in practice.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const LAUNCH_ARGS = ["--use-gl=swiftshader", "--enable-webgl2", "--ignore-gpu-blocklist", "--no-sandbox"];
const N = 200;

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

async function runManyRespawns(landColumns) {
  const { UPDATE_FS } = extractFromParticlesEngine("ui/modules/_streamparticles_gl.js", ["UPDATE_FS"]);
  const fsSource = UPDATE_FS.replace("#version 300 es\n", "#version 300 es\n#define POS_FLOAT 1\n");

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ fsSource, n, landColumns }) => {
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

        // 1x1 inputs -- CLAMP_TO_EDGE means every one of the N output pixels' distinct
        // v_uv samples the SAME single texel, so all N particles share one starting
        // position/age (all forced to reset), differing only via v_uv in their seed.
        const posTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, posTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, new Float32Array([0.9, 0.5, 0, 1]));

        const ageTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        // age=1.0 -> unconditional reset this frame, regardless of position/land.
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([255, 0, 0, 255]));

        // 8x8: the first `landColumns` columns are LAND, rest ocean.
        const W = 8;
        const velData = new Uint8Array(W * W * 4);
        for (let y = 0; y < W; y++) {
          for (let x = 0; x < W; x++) {
            const i = (y * W + x) * 4;
            if (x < landColumns) {
              velData[i + 0] = 128; velData[i + 1] = 128; velData[i + 2] = 0; velData[i + 3] = 0; // land
            } else {
              velData[i + 0] = 140; velData[i + 1] = 128; velData[i + 2] = 0; velData[i + 3] = 255; // ocean
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
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, n, 1, 0, gl.RGBA, gl.FLOAT, null);
        const outAgeTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, outAgeTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, n, 1, 0, gl.RGBA, gl.FLOAT, null);

        const fbo = gl.createFramebuffer();
        gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, outPosTex, 0);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, outAgeTex, 0);
        gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
        if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error("FBO incomplete");
        gl.viewport(0, 0, n, 1);

        gl.useProgram(prog);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, posTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_particles"), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_age"), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_vel"), 2);

        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), 2.5);
        gl.uniform1f(gl.getUniformLocation(prog, "u_speed"), 0.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_seed"), 42.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_landReset"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_ageStep"), 0.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_smoothPx"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_minValue"), 0.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmSpeed"), 0.001);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmDrop"), 0.0);
        gl.uniform4f(gl.getUniformLocation(prog, "u_bboxPos"), 0.0, 0.0, 1.0, 1.0);

        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);

        const posOut = new Float32Array(n * 4);
        gl.readBuffer(gl.COLOR_ATTACHMENT0);
        gl.readPixels(0, 0, n, 1, gl.RGBA, gl.FLOAT, posOut);

        const xs = [];
        for (let i = 0; i < n; i++) xs.push(posOut[i * 4]);
        return xs;
      },
      { fsSource, n: N, landColumns }
    );
  } finally {
    await browser.close();
  }
}

async function main() {
  // Mostly ocean (land = 1/8 of the bbox): the easy case.
  const xsOceanHeavy = await runManyRespawns(1);
  const onLandOceanHeavy = xsOceanHeavy.filter((x) => x < 0.125).length;
  assert(
    onLandOceanHeavy <= 2,
    `mostly-ocean bbox: expected land-avoidance to keep respawns off land across ${N} trials -- ` +
      `got ${onLandOceanHeavy}/${N} respawns on land`
  );

  // Mostly land (land = 7/8 of the bbox, narrow ocean strip at x>=0.875): the regime
  // actually found broken live -- zoomed into a coastline, land can dominate the view.
  const xsLandHeavy = await runManyRespawns(7);
  const onLandLandHeavy = xsLandHeavy.filter((x) => x < 0.875).length;
  assert(
    onLandLandHeavy <= 10,
    `mostly-land bbox: expected the 32-attempt cheap-validity retry to reliably find the ` +
      `narrow ocean strip across ${N} trials (a 6-attempt budget would fail ~38% of the time) -- ` +
      `got ${onLandLandHeavy}/${N} respawns on land`
  );

  console.log("PASS: streamparticles_respawn_land_avoidance");
  console.log(`  mostly-ocean bbox: ${onLandOceanHeavy}/${N} respawns landed on land`);
  console.log(`  mostly-land bbox:  ${onLandLandHeavy}/${N} respawns landed on land`);
}

main().catch((err) => {
  console.error("FAIL: streamparticles_respawn_land_avoidance");
  console.error(err.message);
  process.exit(1);
});
