// Tests for jetstream.js's pure config-mapping helpers. speedFromConfig is a
// PROVISIONAL first-pass estimate (no live-tuning history yet, unlike wind's/
// currents' own heavily-tuned formulas) -- locking its current shape down so a
// future retune is a deliberate, visible change here, not a silent drift.
//
// hFromConfig/coherenceRadius have been through two rounds of live feedback: the
// original hFromConfig (calibrated off currents' magnitude) rendered solid lines with
// no visible particles and jittery trails; wind's own proven range fixed the jitter
// but was still too long at the default; this rescales that range so what rendered at
// trail_length=50 now renders at trail_length=100 (the slider's max). See the comments
// in jetstream.js for the full story.
import { describe, test, expect } from 'vitest';
import { buildLUT, speedFromConfig, hFromConfig, coherenceRadius, paletteFor, LOD_COUNT } from './jetstream.js';

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

    test('interpolates aurora from deep teal-green to violet-magenta', () => {
        const lut = buildLUT('aurora');
        expect(lut[0]).toBe(0);    // round(0.0*255)
        expect(lut[1]).toBe(38);   // round(0.15*255)
        expect(lut[2]).toBe(31);   // round(0.12*255)
        const o = 255 * 4;
        expect(lut[o]).toBe(166);      // round(0.65*255)
        expect(lut[o + 1]).toBe(51);   // round(0.2*255)
        expect(lut[o + 2]).toBe(242);  // round(0.95*255)
    });

    test('interpolates inferno from near-black maroon to bright yellow-white', () => {
        const lut = buildLUT('inferno');
        expect(lut[0]).toBe(20);   // round(0.08*255)
        expect(lut[1]).toBe(0);    // round(0.0*255)
        expect(lut[2]).toBe(5);    // round(0.02*255)
        const o = 255 * 4;
        expect(lut[o]).toBe(255);      // round(1.0*255)
        expect(lut[o + 1]).toBe(230);  // round(0.9*255)
        expect(lut[o + 2]).toBe(102);  // round(0.4*255)
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
    test('maps trail_length 0..100 onto the rescaled 1.65e-5..1.65e-4 arc range', () => {
        expect(hFromConfig({ trail_length: 0 })).toBeCloseTo(1.65e-5);
        expect(hFromConfig({ trail_length: 50 })).toBeCloseTo(9.075e-5);
        expect(hFromConfig({ trail_length: 100 })).toBeCloseTo(1.65e-4);
    });

    test('regression: trail_length=100 now renders what trail_length=50 used to (the value live feedback judged too long as a DEFAULT, now only reachable at the slider max)', () => {
        expect(hFromConfig({ trail_length: 100 })).toBeCloseTo(1.65e-4);
    });

    test('falls back to the midpoint for an out-of-range or missing trail_length', () => {
        expect(hFromConfig({ trail_length: -5 })).toBeCloseTo(9.075e-5);
        expect(hFromConfig({ trail_length: 150 })).toBeCloseTo(9.075e-5);
        expect(hFromConfig({})).toBeCloseTo(9.075e-5);
    });

    test('regression: stays well under the original over-long 2e-4..1.2e-3 range', () => {
        expect(hFromConfig({ trail_length: 100 })).toBeLessThan(2.0e-4);
    });
});

describe('coherenceRadius', () => {
    test('passes through a positive configured radius', () => {
        expect(coherenceRadius({ flow_coherence_radius: 8 })).toBe(8);
    });

    test('disables smoothing (0) for a missing/zero/non-numeric radius', () => {
        expect(coherenceRadius({})).toBe(0);
        expect(coherenceRadius({ flow_coherence_radius: 0 })).toBe(0);
        expect(coherenceRadius({ flow_coherence_radius: 'nonsense' })).toBe(0);
    });

    test('disables smoothing for a negative radius rather than passing it through', () => {
        expect(coherenceRadius({ flow_coherence_radius: -3 })).toBe(0);
    });
});

describe('paletteFor', () => {
    // Regression guard: colormap must re-resolve the palette from whatever cfg is
    // passed in on each call, not a value captured once at mount -- otherwise a live
    // palette change in the config UI silently has no visible effect (the reported bug).
    test('resolves each configured palette name', () => {
        expect(paletteFor({ palette: 'stratosphere' })).toBe('stratosphere');
        expect(paletteFor({ palette: 'aurora' })).toBe('aurora');
        expect(paletteFor({ palette: 'inferno' })).toBe('inferno');
    });

    test('falls back to stratosphere for a missing/unknown palette', () => {
        expect(paletteFor({})).toBe('stratosphere');
        expect(paletteFor({ palette: 'not-a-real-palette' })).toBe('stratosphere');
    });

    test('two different cfg objects resolve independently (no stale capture)', () => {
        expect(paletteFor({ palette: 'inferno' })).not.toBe(paletteFor({ palette: 'aurora' }));
    });
});

describe('LOD_COUNT', () => {
    // Live feedback: the engine's own default LOD_COUNT ({1:4000, 2:9000, 3:18000},
    // tuned for currents spread across open ocean) packed jet-core particles densely
    // enough -- even at the lowest tier -- that overlapping trails read as longer/
    // bunched than they actually are. Halved as a first pass, mirroring wind's own
    // dedicated (lower) LOD_COUNT for the same reason.
    test('is exactly half the engine default at every tier', () => {
        expect(LOD_COUNT).toEqual({ 1: 2000, 2: 4500, 3: 9000 });
    });

    test('every tier stays below the engine default (currents-tuned) LOD_COUNT', () => {
        const engineDefault = { 1: 4000, 2: 9000, 3: 18000 };
        for (const tier of [1, 2, 3]) {
            expect(LOD_COUNT[tier]).toBeLessThan(engineDefault[tier]);
        }
    });
});
