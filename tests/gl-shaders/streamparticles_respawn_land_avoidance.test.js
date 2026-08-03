// Shader-level test for a real bug found live in candidate #7 (particle-engine
// consolidation): UPDATE_FS's respawn destination (randPos) was a pure uniform random
// draw within the view bbox, with NO land-avoidance check at all -- so on a bbox with
// significant land coverage, particles could respawn directly ON land (observed live:
// waves' bars "spawning over land"), independent of any coastline-mask-resolution
// mismatch. Fixed by retrying the random draw (bounded attempts) when u_landReset is on,
// resampling validity each time, falling back to the last candidate if none validate.
//
// Runs the REAL UPDATE_FS against 200 simultaneous particles in one draw call (a wide
// output framebuffer; each pixel's distinct v_uv gives each particle its own
// deterministic random stream from the SAME u_seed, matching real per-frame usage),
// all forced to reset this frame (age>=1), on a texture where land covers exactly 1/8
// of the respawn bbox -- so a single random draw lands on land ~12.5% of the time,
// while 7 total attempts (1 + 6 retries) landing on land simultaneously has probability
// ~0.125^7 (~3.7e-7) -- i.e. it should never happen with the fix across 200 trials, but
// reliably WOULD happen for a meaningful fraction without it.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const LAUNCH_ARGS = ["--use-gl=swiftshader", "--enable-webgl2", "--ignore-gpu-blocklist", "--no-sandbox"];
const N = 200;

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

async function runManyRespawns() {
  const { UPDATE_FS } = extractFromParticlesEngine("ui/modules/_streamparticles_gl.js", ["UPDATE_FS"]);
  const fsSource = UPDATE_FS.replace("#version 300 es\n", "#version 300 es\n#define POS_FLOAT 1\n");

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ fsSource, n }) => {
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

        // 8x8: column x=0 is LAND (1/8 = 12.5% of the respawn bbox), rest ocean.
        const W = 8;
        const velData = new Uint8Array(W * W * 4);
        for (let y = 0; y < W; y++) {
          for (let x = 0; x < W; x++) {
            const i = (y * W + x) * 4;
            if (x === 0) {
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
      { fsSource, n: N }
    );
  } finally {
    await browser.close();
  }
}

async function main() {
  const xs = await runManyRespawns();
  const onLand = xs.filter((x) => x < 0.125).length;

  assert(
    onLand <= 2,
    `expected land-avoidance retry to keep respawns off land across ${N} trials ` +
      `(land is 1/8 of the bbox, so a single unguarded draw would land ~12.5% of the time) -- ` +
      `got ${onLand}/${N} respawns on land`
  );

  console.log("PASS: streamparticles_respawn_land_avoidance");
  console.log(`  ${onLand}/${N} respawns landed on land (land = 1/8 of bbox)`);
}

main().catch((err) => {
  console.error("FAIL: streamparticles_respawn_land_avoidance");
  console.error(err.message);
  process.exit(1);
});
