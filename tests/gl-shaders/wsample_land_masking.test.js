// Verifies sampleWindSmooth's masked-and-renormalized bicubic sampling (architecture
// fix for the coastline velocity dampening found while fixing waves' landReset bug):
// a no-data (alpha=0) neighbour must be excluded from the weighted average, not
// blended in as if it were valid data. Runs the REAL WSAMPLE shader source, extracted
// verbatim from ui/modules/_particles_gl.js -- not a reimplementation.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const FULLSCREEN_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main(){ v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;

const VMAX = 40.0;
// Real encode_uv output for a NaN cell: nan_to_num(NaN)->0, (0+vmax)/(2*vmax)=0.5,
// truncated to uint8 -> 127 (verified against lib/texture.py's actual numpy output).
const REAL_LAND_BYTE = 127;
const OCEAN_BYTE = 140;

function decodedVelocity(byte) {
  return (byte / 255) * 2 * VMAX - VMAX;
}

async function sampleAt(px, py, textureBuilder) {
  const { WSAMPLE } = extractFromParticlesEngine("ui/modules/_particles_gl.js", ["WSAMPLE"]);
  const fsSource = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_wind;
uniform float u_vmax, u_px, u_py;
${WSAMPLE}
void main(){
    vec3 result = sampleWindSmooth(u_wind, vec2(u_px, u_py), u_vmax);
    fragColor = vec4(result, 1.0);
}`;

  const browser = await chromium.launch({
    args: ["--use-gl=swiftshader", "--enable-webgl2", "--ignore-gpu-blocklist", "--no-sandbox"],
  });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ vsSource, fsSource, px, py, W, windData }) => {
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
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));

        const quad = new Float32Array([0,0, 1,0, 0,1, 0,1, 1,0, 1,1]);
        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "a_pos");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

        const windTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, W, W, 0, gl.RGBA, gl.UNSIGNED_BYTE, windData);

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
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_wind"), 0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), 40.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_smoothPx"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_px"), px);
        gl.uniform1f(gl.getUniformLocation(prog, "u_py"), py);

        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);

        const out = new Float32Array(4);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, out);
        return { vx: out[0], vy: out[1], hasData: out[2] };
      },
      { vsSource: FULLSCREEN_VS, fsSource, px, py, W: 8, windData: textureBuilder(8) }
    );
  } finally {
    await browser.close();
  }
}

function landOceanSplitTexture(W) {
  const data = new Uint8Array(W * W * 4);
  for (let y = 0; y < W; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (x === 0) {
        data[i+0] = REAL_LAND_BYTE; data[i+1] = REAL_LAND_BYTE; data[i+2] = 0; data[i+3] = 0;
      } else {
        data[i+0] = OCEAN_BYTE; data[i+1] = OCEAN_BYTE; data[i+2] = 0; data[i+3] = 255;
      }
    }
  }
  return data;
}

function allOceanTexture(W) {
  const data = new Uint8Array(W * W * 4);
  for (let i = 0; i < W * W; i++) {
    data[i*4+0] = OCEAN_BYTE; data[i*4+1] = OCEAN_BYTE; data[i*4+2] = 0; data[i*4+3] = 255;
  }
  return data;
}

function allLandTexture(W) {
  const data = new Uint8Array(W * W * 4);
  for (let i = 0; i < W * W; i++) {
    data[i*4+0] = REAL_LAND_BYTE; data[i*4+1] = REAL_LAND_BYTE; data[i*4+2] = 0; data[i*4+3] = 0;
  }
  return data;
}

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

async function main() {
  const cleanVelocity = decodedVelocity(OCEAN_BYTE);

  // 1. Contamination near a coastline should now be gone (or very small), not the
  //    ~17% dampening measured before the fix.
  const nearCoast = await sampleAt(0.1875, 0.5, landOceanSplitTexture); // 1 texel from land
  const farFromCoast = await sampleAt(0.5625, 0.5, landOceanSplitTexture); // deep ocean
  const dampening = Math.abs(cleanVelocity - nearCoast.vx) / Math.abs(cleanVelocity);
  assert(
    dampening < 0.03,
    `expected near-coast dampening to be eliminated (<3%), got ${(dampening * 100).toFixed(1)}% (vx=${nearCoast.vx}, clean=${cleanVelocity})`
  );
  assert(
    Math.abs(farFromCoast.vx - cleanVelocity) < 0.05,
    `expected far-from-coast sample to be the clean ocean value, got ${farFromCoast.vx} vs ${cleanVelocity}`
  );

  // 2. No regression in a fully-valid (all-ocean) neighbourhood -- renormalization
  //    must be a no-op when every tap is valid (wsum == 1).
  const allOcean = await sampleAt(0.5, 0.5, allOceanTexture);
  assert(
    Math.abs(allOcean.vx - cleanVelocity) < 0.05,
    `expected all-ocean sampling to be unaffected by the fix, got ${allOcean.vx} vs ${cleanVelocity}`
  );

  // 3. Degenerate case: centre tap valid-looking data doesn't occur here (all-land
  //    means c0.a < 0.5 too), so this exercises the c0.a early-return, not the new
  //    wsum guard -- but it must still return a clean zero, not NaN/garbage.
  const allLand = await sampleAt(0.5, 0.5, allLandTexture);
  assert(
    allLand.hasData < 0.5 && Number.isFinite(allLand.vx) && Number.isFinite(allLand.vy),
    `expected an all-land neighbourhood to report no-data cleanly, got ${JSON.stringify(allLand)}`
  );

  console.log("PASS: wsample_land_masking");
  console.log(`  near-coast dampening: ${(dampening * 100).toFixed(2)}% (was ~17% before the fix)`);
  console.log(`  far-from-coast:       vx=${farFromCoast.vx.toFixed(3)} (clean=${cleanVelocity.toFixed(3)})`);
  console.log(`  all-ocean (no fix regression): vx=${allOcean.vx.toFixed(3)}`);
  console.log(`  all-land (degenerate case):    hasData=${allLand.hasData}`);
}

main().catch((err) => {
  console.error("FAIL: wsample_land_masking");
  console.error(err.message);
  process.exit(1);
});
