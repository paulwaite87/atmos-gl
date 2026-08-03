// Regression guard for waves.js's migration onto the shared stream particle engine
// (_streamparticles_gl.js, primitive:'bar') -- candidate #7, particle-engine
// consolidation. Both mapping functions replicate the exact formulas waves previously
// got for free as _particles_gl.js's module defaults (defaultSpeed, and the
// particle_size-based thickness override already present pre-migration); the new
// engine's own defaults differ (speedFromConfig divides by 500 not 1000; thicknessFromConfig
// reads trail_thickness, a field waves' config never sets), so these must be passed
// explicitly to avoid silently changing waves' drift speed / bar thickness.
import { describe, test, expect } from 'vitest';
import { speedFromConfig, thicknessFromConfig } from './waves.js';

describe('speedFromConfig', () => {
    test('maps particle_speed 0..100 onto the pre-migration 0..0.1 drift range', () => {
        expect(speedFromConfig({ particle_speed: 0 })).toBeCloseTo(0.0);
        expect(speedFromConfig({ particle_speed: 100 })).toBeCloseTo(0.1);
        expect(speedFromConfig({ particle_speed: 51 })).toBeCloseTo(0.051);
    });

    test('falls back to the pre-migration default (particle_speed=50) when missing/invalid', () => {
        expect(speedFromConfig({})).toBeCloseTo(0.05);
        expect(speedFromConfig({ particle_speed: -5 })).toBeCloseTo(0.0);   // clamped, not the fallback
        expect(speedFromConfig({ particle_speed: 150 })).toBeCloseTo(0.1); // clamped, not the fallback
    });
});

describe('thicknessFromConfig', () => {
    test('reads particle_size (not trail_thickness), clamped 0.5..5', () => {
        expect(thicknessFromConfig({ particle_size: 1.25 })).toBeCloseTo(1.25);
        expect(thicknessFromConfig({ particle_size: 0.1 })).toBeCloseTo(0.5);
        expect(thicknessFromConfig({ particle_size: 10 })).toBeCloseTo(5.0);
    });

    test('falls back to 1.5 for a missing/invalid particle_size', () => {
        expect(thicknessFromConfig({})).toBeCloseTo(1.5);
        expect(thicknessFromConfig({ particle_size: 'not-a-number' })).toBeCloseTo(1.5);
    });
});
