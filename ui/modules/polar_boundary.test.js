// Tests for polar_boundary.js's levelTag/clampedLevel -- the frontend half of the
// freeze_level_c slider's live texture-switching (see _webglfill.js's cacheKey option
// and PolarBoundaryUpdater.plot()/publish_current_hour() on the backend). levelTag
// MUST mirror tasks/polar_boundary.py's _level_tag() exactly, byte for byte -- any
// drift here silently 404s every non-zero level's texture, since the filename this
// produces has to match what the backend actually wrote to disk.
import { describe, test, expect } from 'vitest';
import { levelTag, clampedLevel } from './polar_boundary.js';

describe('levelTag', () => {
    test('negative levels get an "m" prefix with the absolute value', () => {
        expect(levelTag(-5)).toBe('m5');
        expect(levelTag(-1)).toBe('m1');
    });
    test('positive levels get a "p" prefix', () => {
        expect(levelTag(5)).toBe('p5');
        expect(levelTag(1)).toBe('p1');
    });
    test('zero is the literal string "0", not "p0" or "m0"', () => {
        expect(levelTag(0)).toBe('0');
    });
});

describe('clampedLevel', () => {
    test('rounds a fractional value to the nearest integer', () => {
        expect(clampedLevel({ freeze_level_c: 2.6 })).toBe(3);
        expect(clampedLevel({ freeze_level_c: -2.6 })).toBe(-3);
    });
    test('passes through an in-range integer unchanged', () => {
        expect(clampedLevel({ freeze_level_c: 3 })).toBe(3);
    });
    test('clamps above-range values to the slider max (5)', () => {
        expect(clampedLevel({ freeze_level_c: 999 })).toBe(5);
    });
    test('clamps below-range values to the slider min (-5)', () => {
        expect(clampedLevel({ freeze_level_c: -999 })).toBe(-5);
    });
    test('defaults to 0 when freeze_level_c is missing', () => {
        expect(clampedLevel({})).toBe(0);
    });
    test('defaults to 0 when freeze_level_c is non-numeric', () => {
        expect(clampedLevel({ freeze_level_c: 'nonsense' })).toBe(0);
    });
});
