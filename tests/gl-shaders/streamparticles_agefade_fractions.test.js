// Shader-level test for trailFragmentShader's ageFadeInEnd/ageFadeOutStart parameters
// (candidate #7, particle-engine consolidation) -- added so waves' bar mode can use a
// narrower lifecycle fade window than the streamline ribbons' own 0.20/0.65 default
// without touching currents/jetstream/wind. Runs the REAL trailFragmentShader GLSL via
// a tiny pass-through VS feeding fixed v_speed/v_t/v_age, reading back alpha.
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const LAUNCH_ARGS = ["--use-gl=swiftshader", "--enable-webgl2", "--ignore-gpu-blocklist", "--no-sandbox"];

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

async function runFragmentAt({ ageFadeInEnd, ageFadeOutStart, testAge }) {
  const { trailFragmentShader } = extractFromParticlesEngine("ui/modules/_streamparticles_gl.js", ["trailFragmentShader"]);
  const fsSource = ageFadeInEnd === undefined
    ? trailFragmentShader(0.35)
    : trailFragmentShader(0.35, ageFadeInEnd, ageFadeOutStart);

  const vsSource = `#version 300 es
in vec2 a_pos;
out float v_speed;
out float v_t;
out float v_age;
uniform float u_testAge;
void main(){
    v_speed = 1.0; v_t = 1.0; v_age = u_testAge;
    gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0);
}`;

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ vsSource, fsSource, testAge }) => {
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

        const cmapTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([255, 255, 255, 255]));

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
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, cmapTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_cmap"), 0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_maxspeed"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_alpha"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_calmFade"), 0.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_testAge"), testAge);

        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ZERO);
        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);

        const out = new Float32Array(4);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.FLOAT, out);
        return { alpha: out[3] };
      },
      { vsSource, fsSource, testAge }
    );
  } finally {
    await browser.close();
  }
}

async function main() {
  // At v_age=0.18: inside the DEFAULT fade-in window (0..0.20) -> still ramping up,
  // but PAST a narrower 0.15 window -> already at full fade-in. The two parametrizations
  // must disagree here, proving ageFadeInEnd actually reaches the shader.
  const defaultFractions = await runFragmentAt({ testAge: 0.18 });
  const narrowFractions = await runFragmentAt({ ageFadeInEnd: 0.15, ageFadeOutStart: 0.75, testAge: 0.18 });
  assert(
    narrowFractions.alpha > defaultFractions.alpha,
    `narrow fade-in (0.15) should already be fully faded-in by v_age=0.18, unlike the default (0.20): got narrow=${narrowFractions.alpha}, default=${defaultFractions.alpha}`
  );

  // At v_age=0.70: PAST the default fade-out start (0.65) -> already dimming, but
  // still BEFORE a narrower 0.75 fade-out start -> still at full alpha.
  const defaultAtLateAge = await runFragmentAt({ testAge: 0.70 });
  const narrowAtLateAge = await runFragmentAt({ ageFadeInEnd: 0.15, ageFadeOutStart: 0.75, testAge: 0.70 });
  assert(
    narrowAtLateAge.alpha > defaultAtLateAge.alpha,
    `narrow fade-out (starts 0.75) should still be at full alpha at v_age=0.70, unlike the default (starts 0.65, already dimming): got narrow=${narrowAtLateAge.alpha}, default=${defaultAtLateAge.alpha}`
  );

  // Omitting the new params entirely must reproduce the exact pre-existing default
  // (0.20/0.65) -- a no-op for currents/jetstream/wind's existing calls.
  const omitted = await runFragmentAt({ testAge: 0.18 });
  assert(
    Math.abs(omitted.alpha - defaultFractions.alpha) < 1e-6,
    `omitting ageFadeInEnd/ageFadeOutStart must match explicitly passing the 0.20/0.65 default`
  );

  console.log("PASS: streamparticles_agefade_fractions");
  console.log(`  v_age=0.18: default(0.20) alpha=${defaultFractions.alpha.toFixed(3)}, narrow(0.15) alpha=${narrowFractions.alpha.toFixed(3)}`);
  console.log(`  v_age=0.70: default(0.65) alpha=${defaultAtLateAge.alpha.toFixed(3)}, narrow(0.75) alpha=${narrowAtLateAge.alpha.toFixed(3)}`);
}

main().catch((err) => {
  console.error("FAIL: streamparticles_agefade_fractions");
  console.error(err.message);
  process.exit(1);
});
