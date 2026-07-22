// Regression test for the "particles twitching laterally" bug (diagnosed via a numeric
// port of this engine's RK2/cp_step math against real RTOFS textures): hFromConfig's
// old ~1e-3..7e-3 range made each of the trail engine's 40 fixed segments stride up to
// ~160km per step -- far coarser than the real spatial scale of ocean current
// curvature -- producing a jagged, zig-zagging tail. Locks the new range down so a
// future retune can't silently regress back into that territory.
import { describe, test, expect } from 'vitest';
import { hFromConfig } from './currents.js';

describe('hFromConfig', () => {
    test('maps trail_length 0..100 onto the 1e-4..4e-4 arc range', () => {
        expect(hFromConfig({ trail_length: 0 })).toBeCloseTo(1.0e-4);
        expect(hFromConfig({ trail_length: 50 })).toBeCloseTo(2.5e-4);
        expect(hFromConfig({ trail_length: 100 })).toBeCloseTo(4.0e-4);
    });

    test('regression: stays well under the old 1e-3..7e-3 range that produced jagged tails', () => {
        expect(hFromConfig({ trail_length: 100 })).toBeLessThan(1.0e-3);
    });

    test('falls back to the midpoint for an out-of-range or missing trail_length', () => {
        expect(hFromConfig({ trail_length: -5 })).toBeCloseTo(2.5e-4);
        expect(hFromConfig({ trail_length: 150 })).toBeCloseTo(2.5e-4);
        expect(hFromConfig({})).toBeCloseTo(2.5e-4);
    });
});
