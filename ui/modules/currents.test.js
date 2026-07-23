// Regression test for the "particles twitching laterally" bug (diagnosed via a numeric
// port of this engine's RK2/cp_step math against real RTOFS textures): hFromConfig's
// original ~1e-3..7e-3 range made each of the trail engine's 40 fixed segments stride
// up to ~160km per step -- far coarser than the real spatial scale of ocean current
// curvature -- producing a jagged, zig-zagging tail. A first fix (1e-4..4e-4)
// over-corrected: smoother than the bug, but rendered only ~0.1-0.16x wind's own tail
// length ("hardly any visible length"). This range (6e-4..2.2e-3) balances the two --
// see the call-site comment in currents.js for the measured length/smoothness
// trade-off. Locks it down so a future retune can't silently regress toward either
// failure mode.
import { describe, test, expect } from 'vitest';
import { hFromConfig, paletteFor } from './currents.js';

describe('hFromConfig', () => {
    test('maps trail_length 0..100 onto the 6e-4..2.2e-3 arc range', () => {
        expect(hFromConfig({ trail_length: 0 })).toBeCloseTo(6.0e-4);
        expect(hFromConfig({ trail_length: 50 })).toBeCloseTo(1.4e-3);
        expect(hFromConfig({ trail_length: 100 })).toBeCloseTo(2.2e-3);
    });

    test('regression: stays well under the old 1e-3..7e-3 range that produced jagged tails', () => {
        expect(hFromConfig({ trail_length: 100 })).toBeLessThan(4.0e-3);
    });

    test('regression: stays well above the over-corrected 1e-4..4e-4 range', () => {
        expect(hFromConfig({ trail_length: 0 })).toBeGreaterThan(4.0e-4);
    });

    test('falls back to the midpoint for an out-of-range or missing trail_length', () => {
        expect(hFromConfig({ trail_length: -5 })).toBeCloseTo(1.4e-3);
        expect(hFromConfig({ trail_length: 150 })).toBeCloseTo(1.4e-3);
        expect(hFromConfig({})).toBeCloseTo(1.4e-3);
    });
});

describe('paletteFor', () => {
    // Regression guard: both the fill and particle engines' colormap must re-resolve
    // the palette from whatever cfg is passed in on each call, not a value captured
    // once at mount -- otherwise a live palette change in the config UI silently has no
    // visible effect (the same bug jetstream.js's identical paletteFor fixes).
    test('resolves each configured palette name', () => {
        expect(paletteFor({ palette: 'thermal_red' })).toBe('thermal_red');
        expect(paletteFor({ palette: 'electric_blue' })).toBe('electric_blue');
        expect(paletteFor({ palette: 'toxic_neon' })).toBe('toxic_neon');
        expect(paletteFor({ palette: 'cyberpunk' })).toBe('cyberpunk');
    });

    test('falls back to thermal_red for a missing/unknown palette', () => {
        expect(paletteFor({})).toBe('thermal_red');
        expect(paletteFor({ palette: 'not-a-real-palette' })).toBe('thermal_red');
    });

    test('two different cfg objects resolve independently (no stale capture)', () => {
        expect(paletteFor({ palette: 'cyberpunk' })).not.toBe(paletteFor({ palette: 'toxic_neon' }));
    });
});
