// Shader-level test for the stream particle engine's 'streamline' geometry mode
// (STREAMLINE_VS_BODY, ui/modules/_streamparticles_gl.js) -- previously ZERO shader-
// level coverage existed for this file's UPDATE_FS/trail-VS logic beyond the shared
// VEL_SAMPLE/PACK helpers (candidate #7's task #18: close that gap before checkpoint 1).
// Drives the REAL STREAMLINE_VS_BODY vertex shader via WebGL2 transform feedback
// (captures gl_Position/v_speed/v_t per vertex without rasterizing), against a mock
// identity projectTile so expected screen-space output is exactly computable in JS.
//
// Covers two previously-untested claims from the file's own docstring:
//   1. The tail is traced UPSTREAM from the head -- for eastward flow, the tail-side
//      point of the first ribbon segment must sit WEST of the head.
//   2. A streamline that runs into land freezes (cp_step's `ended` latch) and the
//      resulting coincident segment is discarded (the "meteor" class of artifact this
//      technique was designed to make structurally impossible).
import { chromium } from "playwright";
import { extractFromParticlesEngine } from "./extract_shaders.js";

const IDENTITY_PROJECT_TILE = `vec4 projectTile(vec2 p){ return vec4(p * 2.0 - 1.0, 0.0, 1.0); }`;
const LAUNCH_ARGS = ["--use-gl=swiftshader", "--enable-webgl2", "--ignore-gpu-blocklist", "--no-sandbox"];
const STREAM_STEPS = 40;   // must match _streamparticles_gl.js's own STREAM_STEPS

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

function encodeVel(vx, vy, vmax) {
  const r = Math.round(((vx + vmax) / (2 * vmax)) * 255);
  const g = Math.round(((vy + vmax) / (2 * vmax)) * 255);
  return [r, g, 0, 255];
}

async function runStreamlineQuad({ headX, headY, vmax, halfThick, H, viewport, velTexture }) {
  const { STREAMLINE_VS_BODY } = extractFromParticlesEngine("ui/modules/_streamparticles_gl.js", ["STREAMLINE_VS_BODY"]);
  const vsSource = `#version 300 es\n${IDENTITY_PROJECT_TILE}\n#define POS_FLOAT 1\n${STREAMLINE_VS_BODY}`;
  const fsSource = `#version 300 es
precision highp float;
out vec4 fragColor;
void main(){ fragColor = vec4(1.0); }`;

  const browser = await chromium.launch({ args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    return await page.evaluate(
      ({ vsSource, fsSource, headX, headY, vmax, halfThick, H, viewport, velW, velData }) => {
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
        gl.transformFeedbackVaryings(prog, ["gl_Position", "v_speed", "v_t"], gl.INTERLEAVED_ATTRIBS);
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));

        const headTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, headTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, new Float32Array([headX, headY, 0, 1]));

        const ageTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([64, 0, 0, 255]));

        const velTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, velW, velW, 0, gl.RGBA, gl.UNSIGNED_BYTE, velData);

        const vertCount = 6 * 40;   // STREAM_STEPS baked into the extracted body already
        const tfBuf = gl.createBuffer();
        gl.bindBuffer(gl.TRANSFORM_FEEDBACK_BUFFER, tfBuf);
        gl.bufferData(gl.TRANSFORM_FEEDBACK_BUFFER, vertCount * 6 * 4, gl.STATIC_DRAW);
        gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER, 0, tfBuf);

        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);

        gl.useProgram(prog);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, headTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_head"), 0);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, velTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_vel"), 1);
        gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, ageTex);
        gl.uniform1i(gl.getUniformLocation(prog, "u_age"), 2);
        gl.uniform1f(gl.getUniformLocation(prog, "u_res"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), vmax);
        gl.uniform1f(gl.getUniformLocation(prog, "u_smoothPx"), 1.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_minValue"), 0.0);
        gl.uniform1f(gl.getUniformLocation(prog, "u_halfThick"), halfThick);
        gl.uniform1f(gl.getUniformLocation(prog, "u_H"), H);
        gl.uniform2f(gl.getUniformLocation(prog, "u_viewport"), viewport[0], viewport[1]);

        gl.enable(gl.RASTERIZER_DISCARD);
        gl.beginTransformFeedback(gl.TRIANGLES);
        gl.drawArrays(gl.TRIANGLES, 0, vertCount);
        gl.endTransformFeedback();
        gl.disable(gl.RASTERIZER_DISCARD);

        const out = new Float32Array(vertCount * 6);
        gl.bindBuffer(gl.TRANSFORM_FEEDBACK_BUFFER, tfBuf);
        gl.getBufferSubData(gl.TRANSFORM_FEEDBACK_BUFFER, 0, out);

        const verts = [];
        for (let i = 0; i < vertCount; i++) {
          const o = i * 6;
          verts.push({ x: out[o], y: out[o + 1], z: out[o + 2], w: out[o + 3], speed: out[o + 4], t: out[o + 5] });
        }
        return verts;
      },
      { vsSource, fsSource, headX, headY, vmax, halfThick, H, viewport, velW: velTexture.w, velData: velTexture.data }
    );
  } finally {
    await browser.close();
  }
}

function uniformOceanTexture(w, vx, vy, vmax) {
  const data = new Uint8Array(w * w * 4);
  const [r, g] = encodeVel(vx, vy, vmax);
  for (let i = 0; i < w * w; i++) {
    data[i * 4 + 0] = r; data[i * 4 + 1] = g; data[i * 4 + 2] = 0; data[i * 4 + 3] = 255;
  }
  return { w, data };
}

// West half land (alpha=0), east half ocean (uniform eastward flow) -- a streamline
// heading upstream (west) from a head just inside the ocean side runs into land within
// a handful of steps and must freeze there.
function halfLandTexture(w, vx, vy, vmax) {
  const data = new Uint8Array(w * w * 4);
  const [r, g] = encodeVel(vx, vy, vmax);
  for (let y = 0; y < w; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      const isLand = x < w / 2;
      data[i + 0] = r; data[i + 1] = g; data[i + 2] = 0; data[i + 3] = isLand ? 0 : 255;
    }
  }
  return { w, data };
}

async function main() {
  const vmax = 2.5, viewport = [1000, 1000], halfThick = 2.0;

  // 1. Uniform eastward flow: segment 0's tail-side point must be WEST of the head
  // (upstream), and v_t must match the mix(fOld,fNew,...) formula exactly for both
  // corner groups of the first segment.
  const uniform = await runStreamlineQuad({
    headX: 0.5, headY: 0.5, vmax, halfThick, H: 0.01, viewport,
    velTexture: uniformOceanTexture(4, 1.25, 0.0, vmax),
  });
  const seg0 = uniform.slice(0, 6);
  for (const v of seg0) assert(v.w > 0.9, `segment 0: expected a valid (non-discarded) vertex, got w=${v.w}`);
  assert(seg0.every((v) => Math.abs(v.speed - 1.25) < 0.05), `segment 0: expected v_speed~1.25 for all corners, got ${JSON.stringify(seg0.map((v) => v.speed))}`);

  const fOld = 1.0 - 1 / STREAM_STEPS, fNew = 1.0;
  const tailCorners = [0, 2, 3], headCorners = [1, 4, 5];   // ab[] cc.x==0 vs cc.x==1
  for (const i of tailCorners) assert(Math.abs(seg0[i].t - fOld) < 1e-4, `segment 0 corner ${i}: expected v_t~${fOld}, got ${seg0[i].t}`);
  for (const i of headCorners) assert(Math.abs(seg0[i].t - fNew) < 1e-4, `segment 0 corner ${i}: expected v_t~${fNew}, got ${seg0[i].t}`);

  // Tail-side corners must sit strictly WEST (smaller NDC x) of head-side corners --
  // the streamline traces upstream, so for eastward flow the tail leans west.
  const tailX = tailCorners.map((i) => seg0[i].x);
  const headX = headCorners.map((i) => seg0[i].x);
  assert(Math.max(...tailX) < Math.min(...headX), `segment 0: expected tail-side x (${tailX}) < head-side x (${headX}) for eastward flow`);

  // 2. A streamline that runs into land freezes; a segment well past the freeze point
  // must be discarded (coincident pA/pB -> seg2 < 1e-12 -> sentinel gl_Position).
  const landward = await runStreamlineQuad({
    headX: 0.52, headY: 0.5, vmax, halfThick, H: 0.01, viewport,
    velTexture: halfLandTexture(8, 1.25, 0.0, vmax),
  });
  const lateSegment = landward.slice(6 * 15, 6 * 15 + 6);   // segment 15, well past a ~4-step land hit
  for (const v of lateSegment) {
    assert(v.x >= 1.9 && v.y >= 1.9, `landward segment 15: expected the discard sentinel (2,2,2,1) once frozen on land, got ${JSON.stringify(v)}`);
  }

  console.log("PASS: streamparticles_streamline_geometry");
  console.log(`  segment 0: tail-side v_t=${fOld.toFixed(4)} west of head-side v_t=${fNew}`);
  console.log(`  land-adjacent streamline: segment 15 correctly discarded (frozen/coincident)`);
}

main().catch((err) => {
  console.error("FAIL: streamparticles_streamline_geometry");
  console.error(err.message);
  process.exit(1);
});
