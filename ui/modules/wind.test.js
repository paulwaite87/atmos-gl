// Regression guard for wind.js's finalization onto the shared stream particle engine
// (_streamparticles_gl.js, primitive:'streamline') -- candidate #7, particle-engine
// consolidation, task #24. wind.js already imported createCurrentParticleGLLayer before
// this session (a standalone prototype landed earlier), so this locks down the mapping
// functions already tuned live for wind, and the calm-cell defaults restored from
// _particles_gl.js's pre-migration module defaults (calmDrop=0.06, calmFade=0.6), which
// the new shared engine defaults OFF (0) for every consumer unless opted in.
import { describe, test, expect } from 'vitest';
import { speedFromConfig, coherenceRadius, hFromConfig, thicknessFromConfig, calmDrop, calmFade } from './wind.js';

describe('speedFromConfig', () => {
    test('maps particle_speed 10..100 onto the 0..0.15 drift range', () => {
        expect(speedFromConfig({ particle_speed: 10 })).toBeCloseTo(0.015);
        expect(speedFromConfig({ particle_speed: 100 })).toBeCloseTo(0.15);
        expect(speedFromConfig({ particle_speed: 50 })).toBeCloseTo(0.075);
    });

    test('falls back to the default (particle_speed=50) when missing/out of range', () => {
        expect(speedFromConfig({})).toBeCloseTo(0.075);
        expect(speedFromConfig({ particle_speed: 5 })).toBeCloseTo(0.075);
        expect(speedFromConfig({ particle_speed: 150 })).toBeCloseTo(0.075);
    });
});

describe('coherenceRadius', () => {
    test('reads flow_coherence_radius when positive', () => {
        expect(coherenceRadius({ flow_coherence_radius: 8 })).toBe(8);
    });

    test('falls back to 0 (no smoothing) for missing/invalid/non-positive values', () => {
        expect(coherenceRadius({})).toBe(0);
        expect(coherenceRadius({ flow_coherence_radius: 0 })).toBe(0);
        expect(coherenceRadius({ flow_coherence_radius: -3 })).toBe(0);
    });
});

describe('hFromConfig', () => {
    test('maps trail_length 10..100 onto the ~3e-5..3e-4 arc range', () => {
        expect(hFromConfig({ trail_length: 10 })).toBeCloseTo(3.0e-5 + (10 / 500) * (3.0e-4 - 3.0e-5));
        expect(hFromConfig({ trail_length: 100 })).toBeCloseTo(3.0e-4);
    });

    test('falls back to frac=0.1 for out-of-range or missing trail_length', () => {
        const fallback = 3.0e-5 + 0.1 * (3.0e-4 - 3.0e-5);
        expect(hFromConfig({})).toBeCloseTo(fallback);
        expect(hFromConfig({ trail_length: 5 })).toBeCloseTo(fallback);
        expect(hFromConfig({ trail_length: 150 })).toBeCloseTo(fallback);
    });
});

describe('thicknessFromConfig', () => {
    test('maps trail_thickness 1..5 onto 0.5..1.5px', () => {
        expect(thicknessFromConfig({ trail_thickness: 1 })).toBeCloseTo(0.5);
        expect(thicknessFromConfig({ trail_thickness: 5 })).toBeCloseTo(1.5);
    });

    test('falls back to trail_thickness=3 (1px) for missing/invalid values', () => {
        expect(thicknessFromConfig({})).toBeCloseTo(1.0);
        expect(thicknessFromConfig({ trail_thickness: 'nope' })).toBeCloseTo(1.0);
    });
});

describe('calmDrop', () => {
    test('defaults to 0.06, matching _particles_gl.js pre-migration behaviour', () => {
        expect(calmDrop({})).toBeCloseTo(0.06);
    });

    test('reads a configured calm_drop when present', () => {
        expect(calmDrop({ calm_drop: 0.1 })).toBeCloseTo(0.1);
        expect(calmDrop({ calm_drop: 0 })).toBe(0);
    });
});

describe('calmFade', () => {
    test('defaults to 0.6, matching _particles_gl.js pre-migration behaviour', () => {
        expect(calmFade({})).toBeCloseTo(0.6);
    });

    test('reads a configured calm_fade when present', () => {
        expect(calmFade({ calm_fade: 0.2 })).toBeCloseTo(0.2);
        expect(calmFade({ calm_fade: 0 })).toBe(0);
    });
});
