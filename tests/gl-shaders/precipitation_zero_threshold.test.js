// Regression: a min_mm_hr threshold of exactly 0 must still exclude dry (prate<=0)
// pixels -- "any precipitation, however light" does not mean "show the dry areas
// too". Before the fix, `if (prate < u_min) discard;` with u_min=0 never discarded
// anything (prate can't be negative), painting the whole globe in the lowest band.
// Runs the REAL fragmentBodyFor() source extracted verbatim from
// ui/modules/precipitation.js, not a reimplementation.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

// Sentinel the framebuffer is pre-cleared to; a `discard` leaves it untouched, so a
// readback still equal to this means the fragment was discarded.
const SENTINEL = [0.123, 0.456, 0.789, 0.321];

async function shadeAt(value, uMin) {
  const { fragmentBodyFor } = extractFromParticlesEngine("ui/modules/precipitation.js", [
    "fragmentBodyFor",
  ]);
  const body = fragmentBodyFor("standard");
  const fsSource = `#version 300 es
precision highp float;
out vec4 fragColor;
uniform float u_value;
${body}
void main(){
    fragColor = shade(u_value, vec2(0.0));
}`;
  const vsSource = `#version 300 es
in vec2 a_pos;
void main(){ gl_Position = vec4(a_pos, 0.0, 1.0); }`;

  const browser = await chromium.launch({
    args: ["--use-gl=swiftshader", "--enable-webgl2", "--ignore-gpu-blocklist", "--no-sandbox"],
  });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ vsSource, fsSource, value, uMin, sentinel }) => {
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

        const quad = new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]);
        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "a_pos");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

        const outTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, outTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, null);
        const fbo = gl.createFramebuffer();
        gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, outTex, 0);
        gl.viewport(0, 0, 1, 1);
        gl.clearColor(sentinel[0], sentinel[1], sentinel[2], sentinel[3]);
        gl.clear(gl.COLOR_BUFFER_BIT);

        gl.useProgram(prog);
        gl.uniform1f(gl.getUniformLocation(prog, "u_value"), value);
        gl.uniform1f(gl.getUniformLocation(prog, "u_min"), uMin);
        gl.uniform1f(gl.getUniformLocation(prog, "u_alpha"), 1.0);

        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);

        const out = new Float32Array(4);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, out);
        return Array.from(out);
      },
      { vsSource, fsSource, value, uMin, sentinel: SENTINEL }
    );
  } finally {
    await browser.close();
  }
}

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

const isSentinel = (px) => px.every((v, i) => Math.abs(v - SENTINEL[i]) < 1e-4);

// value = sqrt-encoded position in [0,1]; the shader decodes prate = value*value*100.
// prate=0 -> value=0. prate=0.05 (below the real 0.1mm/hr band floor but > 0) -> small.
const VALUE_ZERO = 0.0;
const VALUE_LIGHT_RAIN = Math.sqrt(0.05 / 100.0); // prate = 0.05 mm/hr

async function main() {
  // u_min = 0 ("any precipitation, however light") must still discard true-dry pixels.
  const dryAtZeroThreshold = await shadeAt(VALUE_ZERO, 0.0);
  assert(
    isSentinel(dryAtZeroThreshold),
    `expected prate=0 with u_min=0 to be discarded (no wash over dry areas), got ${JSON.stringify(dryAtZeroThreshold)}`
  );

  // ...but a genuinely light-rain pixel must still render at u_min=0.
  const lightRainAtZeroThreshold = await shadeAt(VALUE_LIGHT_RAIN, 0.0);
  assert(
    !isSentinel(lightRainAtZeroThreshold),
    `expected prate=0.05 with u_min=0 to render (not be discarded), got ${JSON.stringify(lightRainAtZeroThreshold)}`
  );

  // No regression for an explicit nonzero threshold: below-threshold still discards.
  const belowExplicitThreshold = await shadeAt(VALUE_LIGHT_RAIN, 1.0); // prate=0.05 < min=1.0
  assert(
    isSentinel(belowExplicitThreshold),
    `expected prate=0.05 with u_min=1.0 to be discarded, got ${JSON.stringify(belowExplicitThreshold)}`
  );

  console.log("PASS: precipitation_zero_threshold");
  console.log(`  dry pixel, u_min=0:        discarded (sentinel preserved)`);
  console.log(`  light-rain pixel, u_min=0: rendered`);
  console.log(`  light-rain pixel, u_min=1: discarded (no regression)`);
}

main().catch((err) => {
  console.error("FAIL: precipitation_zero_threshold");
  console.error(err.message);
  process.exit(1);
});
