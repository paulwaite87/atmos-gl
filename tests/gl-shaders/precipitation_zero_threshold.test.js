// Regression: a min_mm_hr threshold of exactly 0 must still exclude dry (value<=0)
// pixels -- "any precipitation, however light" does not mean "show the dry areas
// too". Before an earlier fix, a value<u_min-only test with u_min=0 never excluded
// anything (values can't be negative), painting the whole globe in the lowest band.
//
// precipitation.js's colour logic was later re-engineered to build its LUT via
// buildSteppedLUT() (_thresholdpalette.js) -- the same mechanism pwat/ozone already
// use -- rather than a bespoke per-pixel GLSL discard/band/fwidth shader. That move
// eliminated an entire category of GPU-rendering bugs (bicubic ringing, then
// fwidth() noise amplification, then an unexplained seam) that a hand-rolled
// per-pixel shader kept re-introducing. So this test now exercises the REAL
// buildSteppedLUT() function directly (plain JS, no browser/WebGL needed at all),
// in the SAME sqrt-encoded position space precipitation.js itself builds its LUT in
// (see toSqrtPos there) -- not plain mm/hr, which would round anything below
// ~0.2mm/hr down to the same "zero" LUT entry as true-dry pixels over only 256
// linear entries spanning 0-100mm/hr.
import { buildSteppedLUT } from "../../ui/modules/_thresholdpalette.js";

const VMAX = 100.0;
const LEVELS = [0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0];
const toSqrtPos = (mmPerHour) => Math.sqrt(Math.max(0, mmPerHour) / VMAX);
const LEVELS_SQRT = LEVELS.map(toSqrtPos);
const PALETTE_STANDARD = [
  [0.0, 1.0, 1.0], [0.0, 0.5, 1.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0],
  [1.0, 0.5, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 1.0],
];
const FLAT_COLOR = [0, 0, 0, 0];

function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
}

// Mirrors precipitation.js's own LUT lookup: shade() samples texture(u_cmap,
// vec2(value, 0.5)) directly, where `value` is the sqrt position in [0,1] -- so a
// LUT entry index is just round(sqrtPos * 255).
function lutEntryFor(lut, sqrtPos) {
  const i = Math.max(0, Math.min(255, Math.round(sqrtPos * 255)));
  const o = i * 4;
  return { r: lut[o], g: lut[o + 1], b: lut[o + 2], a: lut[o + 3] };
}

function main() {
  // u_min = 0 ("any precipitation, however light") must still exclude true-dry (0).
  const lutAtZeroThreshold = buildSteppedLUT({
    vmin: 0.0, vmax: 1.0, minValue: toSqrtPos(0.0), levels: LEVELS_SQRT,
    paletteColors: PALETTE_STANDARD, flatColor: FLAT_COLOR,
  });
  const dry = lutEntryFor(lutAtZeroThreshold, toSqrtPos(0.0));
  assert(dry.a === 0, `expected value=0mm/hr with minValue=0 to be transparent (no wash over dry areas), got alpha=${dry.a}`);

  // ...but a genuinely light-rain value (0.05mm/hr, below the 0.1 band floor but > 0)
  // must still render at minValue=0.
  const lightRain = lutEntryFor(lutAtZeroThreshold, toSqrtPos(0.05));
  assert(lightRain.a > 0, `expected value=0.05mm/hr with minValue=0 to render (not transparent), got alpha=${lightRain.a}`);

  // No regression for an explicit nonzero threshold: below-threshold still hides.
  const lutAtExplicitThreshold = buildSteppedLUT({
    vmin: 0.0, vmax: 1.0, minValue: toSqrtPos(1.0), levels: LEVELS_SQRT,
    paletteColors: PALETTE_STANDARD, flatColor: FLAT_COLOR,
  });
  const belowExplicit = lutEntryFor(lutAtExplicitThreshold, toSqrtPos(0.05)); // 0.05 < minValue=1.0
  assert(belowExplicit.a === 0, `expected value=0.05mm/hr with minValue=1.0mm/hr to be transparent, got alpha=${belowExplicit.a}`);

  console.log("PASS: precipitation_zero_threshold");
  console.log("  dry value (0mm/hr), minValue=0:            transparent");
  console.log("  light-rain value (0.05mm/hr), minValue=0:  rendered");
  console.log("  light-rain value (0.05mm/hr), minValue=1:  transparent (no regression)");
}

main();
