// Tests for _thresholdpalette.js -- the ozone/pwat "critical zone" LUT builder,
// mirroring tasks/scalar_field.py's _threshold_colormap() (see its test file,
// tests/test_scalar_field.py, for the Python-side equivalents of these cases).
//
// Domains below use a vmax-vmin span of 51 (255 / 51 = 5 exactly) so every integer
// threshold maps to an exact LUT index -- sampling precisely at the threshold
// boundary is otherwise ambiguous by design: a 1%-wide transition band separates the
// flat and graded sides, and 256-sample rounding can land a step inside that band
// depending on the exact fraction, which is a real (sub-pixel-visible) quantization
// artifact, not a bug. Exact-dividing domains sidestep that ambiguity entirely.
import { describe, test, expect } from 'vitest';
import { buildThresholdLUT } from './_thresholdpalette.js';

function sampleAt(lut, x) {
    const i = Math.round(x * 255) * 4;
    return [lut[i] / 255, lut[i + 1] / 255, lut[i + 2] / 255, lut[i + 3] / 255];
}

function closeTo(actual, expected, tol = 0.02) {
    expected.forEach((v, i) => expect(Math.abs(actual[i] - v)).toBeLessThanOrEqual(tol));
}

describe('buildThresholdLUT', () => {
    test('focus "below" grades toward vmin, flat above threshold', () => {
        const magenta = [1, 0, 1], yellow = [1, 1, 0];
        const flat = [0, 0.1, 0.3, 0.2];
        const lut = buildThresholdLUT({
            vmin: 0, vmax: 51, threshold: 10, focus: 'below',
            paletteColors: [magenta, yellow], flatColor: flat,
        });
        const t = 10 / 51;
        closeTo(sampleAt(lut, 0.0), [...yellow, 1]);
        closeTo(sampleAt(lut, t), [...magenta, 1]);
        closeTo(sampleAt(lut, 1.0), flat);
        closeTo(sampleAt(lut, 0.9), flat);
    });

    test('focus "above" grades toward vmax, flat below threshold', () => {
        const blue = [0, 0, 0.55], violet = [0.6, 0, 0.85];
        const flat = [0, 0, 0, 0];
        const lut = buildThresholdLUT({
            vmin: 0, vmax: 51, threshold: 32, focus: 'above',
            paletteColors: [blue, violet], flatColor: flat,
        });
        const t = 32 / 51;
        closeTo(sampleAt(lut, t), [...blue, 1]);
        closeTo(sampleAt(lut, 1.0), [...violet, 1]);
        closeTo(sampleAt(lut, 0.0), flat);
        closeTo(sampleAt(lut, t - 0.2), flat);
    });

    test('multi-stop palette spans threshold boundary to extreme edge', () => {
        const cyan = [0, 1, 1], magenta = [1, 0, 1];
        const palette = [cyan, [0, 0.5, 1], [0, 1, 0], [1, 1, 0], [1, 0.5, 0], [1, 0, 0], magenta];
        const lut = buildThresholdLUT({
            vmin: 0, vmax: 51, threshold: 32, focus: 'above',
            paletteColors: palette, flatColor: [0, 0, 0, 0],
        });
        const t = 32 / 51;
        closeTo(sampleAt(lut, t), [...cyan, 1]);
        closeTo(sampleAt(lut, 1.0), [...magenta, 1]);
    });

    test('a single-colour palette anchors at the threshold boundary', () => {
        const lut = buildThresholdLUT({
            vmin: 0, vmax: 51, threshold: 20, focus: 'above',
            paletteColors: [[1, 0, 0]], flatColor: [0, 0, 0, 0],
        });
        const t = 20 / 51;
        closeTo(sampleAt(lut, t), [1, 0, 0, 1]);
    });
});
