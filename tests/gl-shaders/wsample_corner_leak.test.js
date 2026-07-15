// Regression test for the coastline-corner particle leak: sampleWindSmooth's coverage
// test (c0) used to read the wind texture via texture() at the particle's exact
// continuous position. That texture is created with gl.LINEAR filtering (for smooth
// velocity elsewhere in the same function). A single straight edge doesn't show any
// leak (the bilinear 50% crossover coincides exactly with the nearest-texel edge
// there) -- but a CONVEX coastline feature (an isolated single-texel headland/island
// tip, ocean on multiple sides) does: for a point at fractional offset (fx,fy) from
// that land texel's own centre, with fx,fy both < 0.5 (so nearest-texel truth is
// still "land"), the bilinear blend is alpha ~ 255*(fx+fy-fx*fy) -- e.g. at
// fx=fy=0.49 (barely inside the land footprint) that's already ~74% ocean-weighted,
// well past the 50% threshold. So most of a small headland's footprint reads as
// "ocean" under the old texture()-based c0, letting particles drift straight through
// it. The fix replaces c0's texture() with texelFetch (always exact per-texel, no
// filtering, regardless of the sampler's LINEAR mode).
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const FULLSCREEN_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main(){ v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;

const VMAX = 40.0;
const OCEAN_BYTE = 140;
const W = 8;

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

// Corner texture: (0,0) is a single isolated LAND texel (a headland/island tip) --
// ocean on every other side, including diagonally. This is the CONVEX case where the
// bilinear blend and nearest-texel classification disagree over most of the land
// texel's own footprint (see the file docstring above).
function cornerTexture() {
  const data = new Uint8Array(W * W * 4);
  for (let y = 0; y < W; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      const isLand = (x === 0 && y === 0);
      if (isLand) {
        data[i + 0] = OCEAN_BYTE; data[i + 1] = OCEAN_BYTE; data[i + 2] = 0; data[i + 3] = 0;
      } else {
        data[i + 0] = OCEAN_BYTE; data[i + 1] = OCEAN_BYTE; data[i + 2] = 0; data[i + 3] = 255;
      }
    }
  }
  return data;
}

async function sampleAt(px, py, linearFilter) {
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
      ({ vsSource, fsSource, px, py, w, windData, linear }) => {
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

        // Matches production's makeWindTex exactly: LINEAR, REPEAT-x, CLAMP-y.
        const filt = linear ? gl.LINEAR : gl.NEAREST;
        const windTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filt);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filt);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, w, 0, gl.RGBA, gl.UNSIGNED_BYTE, windData);

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
      { vsSource: FULLSCREEN_VS, fsSource, px, py, w: W, windData: cornerTexture(), linear: linearFilter }
    );
  } finally {
    await browser.close();
  }
}

async function main() {
  // A point diagonally just inside the land corner's footprint by nearest-texel rules
  // (land texel (0,0) spans x,y in [0, 0.125); this point is well inside that, close
  // to the diagonal where two land texels and the ocean corner texel (1,1) all
  // contribute to the LINEAR blend).
  const cornerPoint = { x: 0.11, y: 0.11 };

  const withLinear = await sampleAt(cornerPoint.x, cornerPoint.y, true);
  const withNearest = await sampleAt(cornerPoint.x, cornerPoint.y, false);

  assert(
    withNearest.hasData < 0.5,
    `sanity check failed: with NEAREST filtering (matches exact per-texel truth), a point ` +
      `inside the land corner's footprint must read hasData < 0.5, got ${withNearest.hasData}`
  );
  assert(
    withLinear.hasData < 0.5,
    `fix regression: with LINEAR filtering (production's actual texture mode), a point ` +
      `inside the land corner's footprint must ALSO read hasData < 0.5 (this is the bug -- ` +
      `texelFetch must make the LINEAR case agree with the NEAREST ground truth), got ${withLinear.hasData}`
  );

  console.log("PASS: wsample_corner_leak");
  console.log(`  corner point (${cornerPoint.x}, ${cornerPoint.y}): LINEAR hasData=${withLinear.hasData}, NEAREST hasData=${withNearest.hasData}`);
}

main().catch((err) => {
  console.error("FAIL: wsample_corner_leak");
  console.error(err.message);
  process.exit(1);
});
