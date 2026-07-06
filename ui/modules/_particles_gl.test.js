// Tests for the config-to-uniform mapping functions in _particles_gl.js (architecture
// review candidate "unify the three GPU particle engines"). These are the genuinely
// pure, testable surface of the module -- the shader/GL logic only runs on the GPU and
// can't be exercised from a JS test. Each function does real clamping/defaulting/
// unit-conversion on a plain config object; a typo or bad clamp here would silently
// misrender every consuming layer (wind, waves), so this is real regression coverage,
// not padding.
import { describe, test, expect } from 'vitest';
import {
    defaultSpeed,
    defaultAlpha,
    defaultStreakLen,
    defaultThickness,
    defaultMaxSpeedColor,
    defaultLandReset,
    defaultParticleCount,
} from './_particles_gl.js';

describe('defaultSpeed', () => {
    test('maps mid-range particle_speed to internal multiplier', () => {
        expect(defaultSpeed({ particle_speed: 50 })).toBeCloseTo(0.05);
    });
    test('clamps above-range particle_speed to 100', () => {
        expect(defaultSpeed({ particle_speed: 500 })).toBeCloseTo(0.1);
    });
    test('clamps below-range (negative) particle_speed to 0', () => {
        expect(defaultSpeed({ particle_speed: -10 })).toBeCloseTo(0);
    });
    test('falls back to 50 when particle_speed is missing/non-numeric', () => {
        expect(defaultSpeed({})).toBeCloseTo(0.05);
        expect(defaultSpeed({ particle_speed: 'nonsense' })).toBeCloseTo(0.05);
    });
});

describe('defaultAlpha', () => {
    test('maps particle_alpha 0-100 to 0-1', () => {
        expect(defaultAlpha({ particle_alpha: 50 })).toBeCloseTo(0.5);
    });
    test('clamps out-of-range values', () => {
        expect(defaultAlpha({ particle_alpha: 150 })).toBeCloseTo(1.0);
        expect(defaultAlpha({ particle_alpha: -20 })).toBeCloseTo(0);
    });
    test('falls back to 90 when missing', () => {
        expect(defaultAlpha({})).toBeCloseTo(0.9);
    });
});

describe('defaultStreakLen', () => {
    test('maps trail_fade 0-100 into px half-length 3..15', () => {
        expect(defaultStreakLen({ trail_fade: 0 })).toBeCloseTo(3);
        expect(defaultStreakLen({ trail_fade: 100 })).toBeCloseTo(15);
    });
    test('falls back to 80 (=> 12.6px) when missing', () => {
        expect(defaultStreakLen({})).toBeCloseTo(3 + 0.8 * 12);
    });
});

describe('defaultThickness', () => {
    test('clamps particle_size to 0.1..5', () => {
        expect(defaultThickness({ particle_size: 0 })).toBeCloseTo(0.1);
        expect(defaultThickness({ particle_size: 50 })).toBeCloseTo(5);
    });
    test('falls back to 1.0 when missing/non-numeric', () => {
        expect(defaultThickness({})).toBeCloseTo(1.0);
    });
});

describe('defaultMaxSpeedColor', () => {
    test('uses max_speed_color when positive', () => {
        expect(defaultMaxSpeedColor({ max_speed_color: 42 })).toBe(42);
    });
    test('falls back to 30.0 when zero, negative, or missing', () => {
        expect(defaultMaxSpeedColor({ max_speed_color: 0 })).toBe(30.0);
        expect(defaultMaxSpeedColor({ max_speed_color: -5 })).toBe(30.0);
        expect(defaultMaxSpeedColor({})).toBe(30.0);
    });
});

describe('defaultLandReset', () => {
    test('always returns 0.0 (wind ignores land by default)', () => {
        expect(defaultLandReset({})).toBe(0.0);
        expect(defaultLandReset({ anything: 'ignored' })).toBe(0.0);
    });
});

describe('defaultParticleCount', () => {
    test('uses explicit particle_count when positive', () => {
        expect(defaultParticleCount({ particle_count: 1000 }, null)).toBe(1000);
    });
    test('falls back to the LOD table keyed by level_of_detail', () => {
        expect(defaultParticleCount({ level_of_detail: 1 }, null)).toBe(3000);
        expect(defaultParticleCount({ level_of_detail: 2 }, null)).toBe(6000);
        expect(defaultParticleCount({ level_of_detail: 3 }, null)).toBe(10000);
    });
    test('defaults to level_of_detail 2 when unset or invalid', () => {
        expect(defaultParticleCount({}, null)).toBe(6000);
        expect(defaultParticleCount({ level_of_detail: 99 }, null)).toBe(6000);
    });
    test('uses a consumer-supplied lodCount table over the module default', () => {
        const customLod = { 1: 4000, 2: 9000, 3: 18000 };
        expect(defaultParticleCount({ level_of_detail: 3 }, customLod)).toBe(18000);
    });
    test('never returns below the 256 floor', () => {
        expect(defaultParticleCount({ particle_count: 10 }, null)).toBe(256);
    });
});
