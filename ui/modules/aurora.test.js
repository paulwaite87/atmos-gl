import { describe, expect, test } from 'vitest';
import { CREDIT, ENCODE_DOMAIN, MIN_VISIBLE, buildAuroraLUT, formatTime, legendGradient, legendLines } from './aurora.js';

// RGBA of the LUT texel the shader samples for a probability (percent).
const lut = buildAuroraLUT();
const texel = (pct) => {
    const i = Math.round(((pct - ENCODE_DOMAIN[0]) / (ENCODE_DOMAIN[1] - ENCODE_DOMAIN[0])) * 255);
    return Array.from(lut.slice(i * 4, i * 4 + 4));
};

describe('the encode domain', () => {
    test('is the fixed 0-100 percent scale the backend texture is written in', () => {
        expect(ENCODE_DOMAIN).toEqual([0, 100]);
        expect(MIN_VISIBLE).toBe(5);
    });
});

describe('buildAuroraLUT', () => {
    test('is a 256-entry RGBA table', () => {
        expect(lut).toBeInstanceOf(Uint8Array);
        expect(lut.length).toBe(256 * 4);
    });

    test('is fully transparent below the 5 percent cutoff', () => {
        for (const pct of [0, 1, 3, 4.5]) expect(texel(pct)[3]).toBe(0);
    });

    test('fades in above the cutoff and is opaque by 16 percent', () => {
        const alphas = [6, 8, 10, 12, 14].map((pct) => texel(pct)[3]);
        expect(alphas[0]).toBeGreaterThan(0);
        expect(alphas).toEqual([...alphas].sort((a, b) => a - b));   // never decreases
        expect(alphas[4]).toBeLessThan(255);
        expect(texel(16)[3]).toBe(255);
        expect(texel(100)[3]).toBe(255);
    });

    test('runs green at the cutoff, through yellow, to red at 100 percent', () => {
        const [r5, g5, b5] = texel(16);          // opaque, low end: green-dominant
        expect(g5).toBeGreaterThan(r5);
        expect(g5).toBeGreaterThan(b5);

        const [r40, g40] = texel(40);            // yellow: red and green both high
        expect(r40).toBeGreaterThan(200);
        expect(g40).toBeGreaterThan(200);

        const [r100, g100, b100] = texel(100);   // red: red high, green low
        expect(r100).toBeGreaterThan(200);
        expect(g100).toBeLessThan(80);
        expect(b100).toBeLessThan(80);
    });

    test('moves from yellow toward red as the probability rises', () => {
        const greens = [40, 55, 70, 85, 100].map((pct) => texel(pct)[1]);
        expect(greens).toEqual([...greens].sort((a, b) => b - a));   // green falls steadily
    });
});

describe('formatTime', () => {
    test('shows the feed time in UTC as day, month and clock', () => {
        expect(formatTime('2026-10-17T05:53:00Z')).toBe('17 Oct 05:53 UTC');
        expect(formatTime('2026-01-02T23:07:00Z')).toBe('2 Jan 23:07 UTC');
    });

    test('passes through a time it cannot read rather than hiding it', () => {
        expect(formatTime('soon')).toBe('soon');
    });
});

describe('legendLines', () => {
    test('names the layer and gives the observation and forecast times', () => {
        const meta = { aurora: { observed: '2026-10-17T05:53:00Z', forecast: '2026-10-17T06:54:00Z' } };
        expect(legendLines(meta)).toEqual({
            title: 'Aurora probability',
            observed: 'Observed 17 Oct 05:53 UTC',
            forecast: 'Forecast 17 Oct 06:54 UTC',
            credit: 'NOAA SWPC, OVATION',
        });
        expect(CREDIT).toBe('NOAA SWPC, OVATION');
    });

    test('still shows the layer and credit when the times are not available', () => {
        const lines = legendLines({});
        expect(lines.title).toBe('Aurora probability');
        expect(lines.observed).toBeNull();
        expect(lines.forecast).toBeNull();
        expect(lines.credit).toBe('NOAA SWPC, OVATION');
    });
});

describe('legendGradient', () => {
    test('is a left-to-right CSS gradient from green through yellow to red', () => {
        const css = legendGradient();
        expect(css.startsWith('linear-gradient(to right,')).toBe(true);
        const colours = css.match(/#[0-9a-f]{6}/gi);
        expect(colours.length).toBeGreaterThanOrEqual(3);
        expect(css.indexOf(colours[0])).toBeLessThan(css.indexOf(colours[colours.length - 1]));
    });
});
