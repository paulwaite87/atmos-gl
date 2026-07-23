// Tests for jetstream.js's pure config-mapping helpers. speedFromConfig/hFromConfig
// are PROVISIONAL first-pass estimates (no live-tuning history yet, unlike wind's/
// currents' own heavily-tuned formulas) -- locking their current shape down so a
// future retune is a deliberate, visible change here, not a silent drift.
import { describe, test, expect } from 'vitest';
import { buildLUT, speedFromConfig, hFromConfig } from './jetstream.js';

describe('buildLUT', () => {
    test('returns a 256-entry RGBA lookup table', () => {
        const lut = buildLUT('stratosphere');
        expect(lut.length).toBe(256 * 4);
    });

    test('alpha is always fully opaque', () => {
        const lut = buildLUT('stratosphere');
        for (let i = 0; i < 256; i++) expect(lut[i * 4 + 3]).toBe(255);
    });

    test('interpolates from the indigo start color at i=0', () => {
        const lut = buildLUT('stratosphere');
        expect(lut[0]).toBe(13);   // round(0.05*255)
        expect(lut[1]).toBe(13);   // round(0.05*255)
        expect(lut[2]).toBe(89);   // round(0.35*255)
    });

    test('interpolates to the near-white end color at i=255', () => {
        const lut = buildLUT('stratosphere');
        const o = 255 * 4;
        expect(lut[o]).toBe(217);      // round(0.85*255)
        expect(lut[o + 1]).toBe(242);  // round(0.95*255)
        expect(lut[o + 2]).toBe(255);  // round(1.0*255)
    });

    test('falls back to stratosphere for an unknown palette name', () => {
        expect(buildLUT('not-a-real-palette')).toEqual(buildLUT('stratosphere'));
    });
});

describe('speedFromConfig', () => {
    test('maps particle_speed 0..100 onto the 0..0.2 advection range', () => {
        expect(speedFromConfig({ particle_speed: 0 })).toBeCloseTo(0);
        expect(speedFromConfig({ particle_speed: 50 })).toBeCloseTo(0.1);
        expect(speedFromConfig({ particle_speed: 100 })).toBeCloseTo(0.2);
    });

    test('clamps an out-of-range particle_speed instead of extrapolating', () => {
        expect(speedFromConfig({ particle_speed: -20 })).toBeCloseTo(0);
        expect(speedFromConfig({ particle_speed: 500 })).toBeCloseTo(0.2);
    });

    test('defaults to the midpoint for a missing/non-numeric particle_speed', () => {
        expect(speedFromConfig({})).toBeCloseTo(0.1);
        expect(speedFromConfig({ particle_speed: 'nonsense' })).toBeCloseTo(0.1);
    });
});

describe('hFromConfig', () => {
    test('maps trail_length 0..100 onto the 2e-4..1.2e-3 arc range', () => {
        expect(hFromConfig({ trail_length: 0 })).toBeCloseTo(2.0e-4);
        expect(hFromConfig({ trail_length: 50 })).toBeCloseTo(7.0e-4);
        expect(hFromConfig({ trail_length: 100 })).toBeCloseTo(1.2e-3);
    });

    test('falls back to the midpoint for an out-of-range or missing trail_length', () => {
        expect(hFromConfig({ trail_length: -5 })).toBeCloseTo(7.0e-4);
        expect(hFromConfig({ trail_length: 150 })).toBeCloseTo(7.0e-4);
        expect(hFromConfig({})).toBeCloseTo(7.0e-4);
    });
});
