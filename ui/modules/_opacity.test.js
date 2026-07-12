// Tests for opacityUniform, the config-to-uniform mapping shared by every scalar-field
// and vector layer's opacity handling (architecture review candidate "extract
// opacityUniform from seven copies"). A typo or bad clamp here would silently misrender
// every consuming layer, so this is real regression coverage, not padding -- in
// particular the zero case, which is the exact bug that shipped in six of the seven
// duplicated copies this module replaces.
import { describe, test, expect } from 'vitest';
import { opacityUniform } from './_opacity.js';

describe('opacityUniform', () => {
    test('maps opacity 0-100 to 0-1', () => {
        expect(opacityUniform({ opacity: 50 }, 0.85)).toBeCloseTo(0.5);
    });
    test('honours an explicit 0 -- must not fall back to the default', () => {
        expect(opacityUniform({ opacity: 0 }, 0.85)).toBeCloseTo(0);
    });
    test('falls back to the given default when opacity is missing', () => {
        expect(opacityUniform({}, 0.85)).toBeCloseTo(0.85);
    });
    test('falls back to the given default when opacity is non-numeric', () => {
        expect(opacityUniform({ opacity: 'nonsense' }, 0.85)).toBeCloseTo(0.85);
    });
    test('clamps below-range (negative) opacity to 0', () => {
        expect(opacityUniform({ opacity: -20 }, 0.85)).toBeCloseTo(0);
    });
    test('clamps above-range opacity to 1', () => {
        expect(opacityUniform({ opacity: 150 }, 0.85)).toBeCloseTo(1.0);
    });
});
