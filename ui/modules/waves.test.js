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
import { captureParticleControllerOpts } from '../../tests/gl-shaders/extract_shaders.js';

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

// Wiring: does loadLayer's REAL createCurrentParticleGLLayer call actually pass what's
// tested above -- in particular, that waves really opts OUT of calmDrop/calmFade
// (see 0aeee90's unit-mismatch reasoning), not just that the reasoning is documented in
// a comment (architecture review candidate D). captureParticleControllerOpts re-
// evaluates waves.js's source in a sandboxed vm realm separate from this test's own ES
// import, so captured functions compare by .toString() (same source), not reference.
describe('createCurrentParticleGLLayer wiring', () => {
    test('uses bar primitive mode -- crest ticks, not a streamline ribbon', async () => {
        const opts = await captureParticleControllerOpts('ui/modules/waves.js', 'loadLayer');
        expect(opts.primitive).toBe('bar');
    });

    test('does not set calmDrop/calmFade -- deliberate opt-out, not an oversight', async () => {
        const opts = await captureParticleControllerOpts('ui/modules/waves.js', 'loadLayer');
        expect(opts.calmDrop).toBeUndefined();
        expect(opts.calmFade).toBeUndefined();
    });

    test('sets landReset to 1 -- bars must avoid land like currents, unlike wind/jetstream', async () => {
        const opts = await captureParticleControllerOpts('ui/modules/waves.js', 'loadLayer');
        expect(opts.landReset({})).toBe(1.0);
    });

    test('passes the tuned mapper functions, not a re-implementation', async () => {
        const opts = await captureParticleControllerOpts('ui/modules/waves.js', 'loadLayer');
        expect(opts.speedFromConfig.toString()).toBe(speedFromConfig.toString());
        expect(opts.thicknessFromConfig.toString()).toBe(thicknessFromConfig.toString());
    });

    test('sets a live min_wave_height threshold (minValue), unlike wind/currents/jetstream', async () => {
        const opts = await captureParticleControllerOpts('ui/modules/waves.js', 'loadLayer');
        expect(opts.minValue({ min_wave_height: 1.5 })).toBeCloseTo(1.5);
        expect(opts.minValue({})).toBe(0);
    });
});
