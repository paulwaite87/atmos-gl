import { describe, expect, test } from 'vitest';
import { CREDIT, formatImageDate, legendTitle, opacityFor, rampGradient, RAMP_STOPS } from './night_lights.js';

describe('formatImageDate', () => {
    test('renders an ISO date as day, short month, year', () => {
        expect(formatImageDate('2026-10-15')).toBe('15 Oct 2026');
        expect(formatImageDate('2026-01-03')).toBe('3 Jan 2026');
    });

    test('returns an empty string for a missing or malformed date', () => {
        expect(formatImageDate(null)).toBe('');
        expect(formatImageDate('not a date')).toBe('');
    });
});

describe('legendTitle', () => {
    test('states the image date so a stale image is visibly stale', () => {
        expect(legendTitle('2026-10-15')).toBe('Night lights · 15 Oct 2026');
    });

    test('falls back to the bare name before the date is known', () => {
        expect(legendTitle(null)).toBe('Night lights');
    });
});

describe('credit', () => {
    test('names NASA Black Marble, GIBS and VIIRS Suomi NPP', () => {
        expect(CREDIT).toBe('NASA Black Marble / GIBS, VIIRS Suomi NPP');
    });
});

describe('ramp', () => {
    test('runs from deep orange through yellow to near-white', () => {
        expect(RAMP_STOPS.map((s) => s.color)).toEqual(['#cd4b0a', '#ffbe32', '#fffaeb']);
        expect(RAMP_STOPS.map((s) => s.at)).toEqual([0, 55, 100]);
    });

    test('is expressed as a left-to-right CSS gradient', () => {
        expect(rampGradient()).toBe('linear-gradient(to right, #cd4b0a 0%, #ffbe32 55%, #fffaeb 100%)');
    });
});

describe('opacityFor', () => {
    test('converts the 0-100 UI scale to 0-1', () => {
        expect(opacityFor({ opacity: 85 })).toBe(0.85);
        expect(opacityFor({ opacity: 0 })).toBe(0);
        expect(opacityFor({ opacity: 250 })).toBe(1);
    });

    test('defaults to 0.85 when unset', () => {
        expect(opacityFor({})).toBe(0.85);
        expect(opacityFor(undefined)).toBe(0.85);
    });
});
