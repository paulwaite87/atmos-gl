// Streamline-engine counterpart to particles_land_reset.test.js -- same land-reset
// regression (a particle on a no-data/land texel must be reset to a new position when
// landReset is on, not left to sit there indefinitely), run against
// ui/modules/_streamparticles_gl.js's own UPDATE_FS instead of _particles_gl.js's.
// Closes candidate #7's task #18 shader-test gap: UPDATE_FS here previously had zero
// coverage beyond the shared VEL_SAMPLE/PACK helpers and the calm-cell-specific paths
// added in streamparticles_calm_cell.test.js.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const LAUNCH_ARGS = ["--use-gl=swiftshader", "--enable-webgl2", "--ignore-gpu-blocklist", "--no-sandbox"];

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

async function runUpdateShaderOnLandParticle(landReset) {
  const { UPDATE_FS } = extractFromParticlesEngine("ui/modules/_streamparticles_gl.js", ["UPDATE_FS"]);
  const fsSource = UPDATE_FS.replace("#version 300 es\n", "#version 300 es\n#define POS_FLOAT 1\n");

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ vsSource, fsSource, landReset }) => {
        const canvas = document.createElement("canvas");
        canvas.width = 1; canvas.height = 1;
        const gl = canvas.getContext("webgl2");
        gl.getExtension("EXT_color_buffer_float");

        function compile(type, src) {
          const sh = gl.createShader(type);
          gl.shaderSource(sh, src);
          gl.compileShader(sh);
          if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error("shader compile failed: " + gl.getShaderInfoLog(sh));
          return sh;
        }
        const prog = gl.createProgram();
        gl.attachShader(prog, compile(gl.VERTEX_SHADER, vsSource));
        gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fsSource));
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error("link failed: " + gl.getProgramInfoLog(prog));

        const quad = new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]);
        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "a_pos");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

        // Single test particle, sitting exactly on a LAND texel centre.
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

        // 8x8 velocity texture: column x=0 is LAND (alpha=0), rest is clean ocean.
        const W = 8;
        const velData = new Uint8Array(W * W * 4);
        for (let y = 0; y < W; y++) {
          for (let x = 0; x < W; x++) {
            const i = (y * W + x) * 4;
            if (x === 0) {
              velData[i + 0] = 128; velData[i + 1] = 128; velData[i + 2] = 0; velData[i + 3] = 0; // land
            } else {
              velData[i + 0] = 140; velData[i + 1] = 140; velData[i + 2] = 0; velData[i + 3] = 255; // ocean
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

        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), 2.5);
        gl.uniform1f(gl.getUniformLocation(prog, "u_speed"), 0.4);
        gl.uniform1f(gl.getUniformLocation(prog, "u_seed"), 12.34);
        gl.uniform1f(gl.getUniformLocation(prog, "u_landReset"), landReset);
        gl.uniform1f(gl.getUniformLocation(prog, "u_ageStep"), 0.02);
        gl.uniform1f(gl.getUniformLocation(prog, "u_smoothPx"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_minValue"), 0.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmSpeed"), 0.001); // negligible calm-reset
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmDrop"), 0.0);    // disable calm-reset confound
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
      { vsSource: `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main(){ v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`, fsSource, landReset }
    );
  } finally {
    await browser.close();
  }
}

async function main() {
  const off = await runUpdateShaderOnLandParticle(0.0);
  const on = await runUpdateShaderOnLandParticle(1.0);

  assert(
    Math.abs(off.outPos[0] - off.startPos[0]) < 1e-4 && Math.abs(off.outPos[1] - off.startPos[1]) < 1e-4,
    `landReset=0: expected the particle to stay stuck at its land position ${JSON.stringify(off.startPos)}, got ${JSON.stringify(off.outPos)}`
  );
  assert(off.outAge > 0.1, `landReset=0: expected age to have advanced past its 0.5 start (not reset), got ${off.outAge}`);
  assert(on.outAge < 0.1, `landReset=1: expected the particle to be reset (age -> ~0), got ${on.outAge}`);
  assert(
    Math.abs(on.outPos[0] - on.startPos[0]) > 1e-3 || Math.abs(on.outPos[1] - on.startPos[1]) > 1e-3,
    `landReset=1: expected the particle to be moved to a new position, stayed at ${JSON.stringify(on.outPos)}`
  );

  console.log("PASS: streamparticles_update_land_reset");
  console.log(`  landReset=0 (stuck on land): pos=${JSON.stringify(off.outPos)}, age=${off.outAge.toFixed(3)}`);
  console.log(`  landReset=1 (reset away):    pos=${JSON.stringify(on.outPos)}, age=${on.outAge.toFixed(3)}`);
}

main().catch((err) => {
  console.error("FAIL: streamparticles_update_land_reset");
  console.error(err.message);
  process.exit(1);
});
