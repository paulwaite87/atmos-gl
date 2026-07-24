// Tests for flightradar.js's pure dead-reckoning + freeze-clamp helpers (issue #203,
// docs/adr/0009). Expected values are worked by hand from first principles (60 knots
// for 1 hour = 60 nautical miles = exactly 1 degree of latitude), not recomputed the
// way the code does, so a broken formula can actually disagree with the test.
import { describe, test, expect } from 'vitest';
import { interpolatedPosition, boundedElapsedSeconds, isFrozen } from './flightradar.js';

describe('interpolatedPosition', () => {
    test('due-north flight for 1 hour at 60kts moves exactly 1 degree of latitude', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 60, track: 0 }, 3600);
        expect(pos.lat).toBeCloseTo(11.0, 6);
        expect(pos.lon).toBeCloseTo(20.0, 6);
    });

    test('due-south flight moves latitude negative', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 60, track: 180 }, 3600);
        expect(pos.lat).toBeCloseTo(9.0, 6);
        expect(pos.lon).toBeCloseTo(20.0, 6);
    });

    test('due-east flight at the equator moves exactly 1 degree of longitude', () => {
        const pos = interpolatedPosition({ lat: 0, lon: 20, gs: 60, track: 90 }, 3600);
        expect(pos.lat).toBeCloseTo(0.0, 6);
        expect(pos.lon).toBeCloseTo(21.0, 6);
    });

    test('due-east flight at 60deg latitude moves 2 degrees of longitude (convergence)', () => {
        const pos = interpolatedPosition({ lat: 60, lon: 20, gs: 60, track: 90 }, 3600);
        expect(pos.lat).toBeCloseTo(60.0, 6);
        expect(pos.lon).toBeCloseTo(22.0, 6);
    });

    test('zero elapsed time means no movement', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 400, track: 45 }, 0);
        expect(pos).toEqual({ lat: 10, lon: 20 });
    });

    test('zero ground speed means no movement', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 0, track: 45 }, 100);
        expect(pos).toEqual({ lat: 10, lon: 20 });
    });

    test('missing track (no heading data) means no movement', () => {
        const pos = interpolatedPosition({ lat: 10, lon: 20, gs: 400, track: undefined }, 100);
        expect(pos).toEqual({ lat: 10, lon: 20 });
    });
});

describe('boundedElapsedSeconds', () => {
    test('returns real elapsed seconds when under the cap', () => {
        expect(boundedElapsedSeconds(0, 3000, 10)).toBeCloseTo(3.0, 6);
    });

    test('clamps to the cap once elapsed time exceeds it', () => {
        expect(boundedElapsedSeconds(0, 60000, 10)).toBe(10);
    });

    test('never returns negative (a lastSeen in the future, e.g. clock skew)', () => {
        expect(boundedElapsedSeconds(5000, 0, 10)).toBe(0);
    });

    test('defaults the cap to MAX_EXTRAPOLATION_S when omitted', () => {
        expect(boundedElapsedSeconds(0, 3000)).toBeCloseTo(3.0, 6);
    });
});

describe('isFrozen', () => {
    test('not frozen while under the cap', () => {
        expect(isFrozen(0, 3000, 10)).toBe(false);
    });

    test('frozen once elapsed time reaches the cap', () => {
        expect(isFrozen(0, 10000, 10)).toBe(true);
    });

    test('frozen once elapsed time exceeds the cap', () => {
        expect(isFrozen(0, 60000, 10)).toBe(true);
    });

    test('defaults the cap to MAX_EXTRAPOLATION_S when omitted', () => {
        expect(isFrozen(0, 3000)).toBe(false);
        expect(isFrozen(0, 60000)).toBe(true);
    });
});
