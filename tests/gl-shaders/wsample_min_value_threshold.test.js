// Verifies sampleWindSmooth's live u_minValue threshold (added for waves'
// min_wave_height, see WSAMPLE's docstring in _particles_gl.js): a cell whose decoded
// magnitude is below u_minValue must be treated as no-data, exactly like land --
// hasData=0 -- so it participates identically in every consumer that already branches
// on hasData<0.5 (UPDATE_FS's reset test, both draw shaders' discards). u_minValue=0
// (the default for every consumer that doesn't set it, e.g. wind) must be a no-op.
// Runs the REAL WSAMPLE shader source, extracted verbatim from ui/modules/_particles_gl.js.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const FULLSCREEN_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main(){ v_uv = a_pos; gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); }`;

const VMAX = 8.0;   // waves' VMAX_WAVES
const W = 8;

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

// Uniform ocean texture: every cell has u=SPEED, v=0 (encoded), alpha=255 (not land).
function uniformSpeedTexture(speed) {
  const data = new Uint8Array(W * W * 4);
  const ru = Math.round(((speed + VMAX) / (2 * VMAX)) * 255);
  const rv = Math.round((VMAX / (2 * VMAX)) * 255);  // v=0
  for (let i = 0; i < W * W; i++) {
    data[i * 4 + 0] = ru; data[i * 4 + 1] = rv; data[i * 4 + 2] = 0; data[i * 4 + 3] = 255;
  }
  return data;
}

async function sampleAt(px, py, minValue) {
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
      ({ vsSource, fsSource, px, py, w, windData, minValue: mv }) => {
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

        // Matches production's makeWindTex: LINEAR, REPEAT-x, CLAMP-y.
        const windTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, windTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
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
        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), 8.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_smoothPx"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_minValue"), mv);
        gl.uniform1f(gl.getUniformLocation(prog, "u_px"), px);
        gl.uniform1f(gl.getUniformLocation(prog, "u_py"), py);

        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);

        const out = new Float32Array(4);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, out);
        return { vx: out[0], vy: out[1], hasData: out[2] };
      },
      { vsSource: FULLSCREEN_VS, fsSource, px, py, w: W, windData: uniformSpeedTexture(3.0), minValue }
    );
  } finally {
    await browser.close();
  }
}

async function main() {
  const p = { x: 0.5, y: 0.5 };  // deep in the uniform-3.0-m/s texture, not near any edge

  const disabled = await sampleAt(p.x, p.y, 0.0);
  assert(
    disabled.hasData >= 0.5 && Math.abs(disabled.vx - 3.0) < 0.05,
    `u_minValue=0 (disabled, wind's default) must be a no-op: expected hasData>=0.5 and vx~3.0, got ${JSON.stringify(disabled)}`
  );

  const belowSpeed = await sampleAt(p.x, p.y, 1.0);   // threshold below the data's 3.0 m/s
  assert(
    belowSpeed.hasData >= 0.5,
    `u_minValue=1.0 < data speed 3.0: must still read as has-data, got ${JSON.stringify(belowSpeed)}`
  );

  const aboveSpeed = await sampleAt(p.x, p.y, 5.0);   // threshold above the data's 3.0 m/s
  assert(
    aboveSpeed.hasData < 0.5,
    `u_minValue=5.0 > data speed 3.0: must read as no-data (same as land), got ${JSON.stringify(aboveSpeed)}`
  );

  console.log("PASS: wsample_min_value_threshold");
  console.log(`  disabled (u_minValue=0):        hasData=${disabled.hasData}, vx=${disabled.vx.toFixed(3)}`);
  console.log(`  below data speed (u_minValue=1): hasData=${belowSpeed.hasData}`);
  console.log(`  above data speed (u_minValue=5): hasData=${aboveSpeed.hasData}`);
}

main().catch((err) => {
  console.error("FAIL: wsample_min_value_threshold");
  console.error(err.message);
  process.exit(1);
});
