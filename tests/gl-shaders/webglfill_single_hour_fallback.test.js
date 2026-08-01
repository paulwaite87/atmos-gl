// Regression: render() used to require BOTH the current hour (e0) and next hour
// (e1, needed only for smooth inter-hour interpolation) to be ready before drawing
// anything at all -- so a render backlog that has the current hour ready but not yet
// its neighbour (e.g. right after a cache-clearing restart, or simply the newest
// just-published hour) blanked the whole layer even though there was real, correct
// data to show. selectRenderTextures() is the extracted (pure, no GL/map dependency)
// decision logic; this runs the REAL function from ui/modules/_webglfill.js via a
// sandboxed vm eval, not a reimplementation.
import { extractFromParticlesEngine } from "./extract_shaders.js";

const { selectRenderTextures } = extractFromParticlesEngine("ui/modules/_webglfill.js", [
  "selectRenderTextures",
]);

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

const e0 = { tex: "tex-hour-0", ready: true };
const e1 = { tex: "tex-hour-1", ready: true };
const notReady = { tex: "tex-loading", ready: false };

function main() {
  // Both ready, playing: real cross-fade between the two hours.
  let sel = selectRenderTextures(e0, e1, true, 0.42);
  assert(sel.texA === e0 && sel.texB === e1 && sel.frac === 0.42, "expected cross-fade when both ready and playing");

  // Both ready, paused: frac pinned to 0 (show current hour only), matching prior behaviour.
  sel = selectRenderTextures(e0, e1, false, 0.42);
  assert(sel.texA === e0 && sel.texB === e1 && sel.frac === 0.0, "expected frac=0 when paused even with both ready");

  // Only e0 ready (e1 still loading/missing) -- must render e0 alone, not blank.
  sel = selectRenderTextures(e0, notReady, true, 0.42);
  assert(sel.texA === e0 && sel.texB === e0 && sel.frac === 0.0, "expected single-hour fallback to e0 when e1 not ready");

  sel = selectRenderTextures(e0, null, true, 0.42);
  assert(sel.texA === e0 && sel.texB === e0 && sel.frac === 0.0, "expected single-hour fallback to e0 when e1 is null");

  // Only e1 ready (e0 still loading/missing) -- must render e1 alone, not blank.
  sel = selectRenderTextures(notReady, e1, true, 0.42);
  assert(sel.texA === e1 && sel.texB === e1 && sel.frac === 0.0, "expected single-hour fallback to e1 when e0 not ready");

  // Neither ready -- genuinely nothing to draw.
  sel = selectRenderTextures(notReady, notReady, true, 0.42);
  assert(sel === null, "expected null when neither hour is ready");

  sel = selectRenderTextures(null, null, true, 0.42);
  assert(sel === null, "expected null when both entries are null");

  console.log("PASS: webglfill_single_hour_fallback");
  console.log("  both ready, playing:  cross-fades");
  console.log("  both ready, paused:   frac=0");
  console.log("  only e0 ready:        renders e0 alone (not blank)");
  console.log("  only e1 ready:        renders e1 alone (not blank)");
  console.log("  neither ready:        null (nothing to draw)");
}

main();
