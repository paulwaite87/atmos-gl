import { describe, expect, test } from 'vitest';
import { CREDIT, ENCODE_DOMAIN, MIN_VISIBLE, buildSeaIceLUT, formatDate, legendGradient, legendLines } from './sea_ice.js';

// RGBA of the LUT texel the shader samples for a concentration (percent).
const lut = buildSeaIceLUT();
const texel = (pct) => {
    const i = Math.round(((pct - ENCODE_DOMAIN[0]) / (ENCODE_DOMAIN[1] - ENCODE_DOMAIN[0])) * 255);
    return Array.from(lut.slice(i * 4, i * 4 + 4));
};

describe('the encode domain', () => {
    test('is the fixed 0-100 percent scale the backend texture is written in', () => {
        expect(ENCODE_DOMAIN).toEqual([0, 100]);
        expect(MIN_VISIBLE).toBe(15);   // the standard ice-edge definition
    });
});

describe('buildSeaIceLUT', () => {
    test('is a 256-entry RGBA table', () => {
        expect(lut).toBeInstanceOf(Uint8Array);
        expect(lut.length).toBe(256 * 4);
    });

    test('is fully transparent below the 15 percent ice edge', () => {
        for (const pct of [0, 5, 10, 14]) expect(texel(pct)[3]).toBe(0);
    });

    test('is translucent pale blue just above the edge', () => {
        const [r, g, b, a] = texel(16);
        expect(a).toBeGreaterThan(0);
        expect(a).toBeLessThan(160);                 // clearly translucent
        expect(b).toBeGreaterThan(g);                // blue-dominant...
        expect(g).toBeGreaterThan(r);
        expect(r).toBeGreaterThan(100);              // ...but pale, not saturated
    });

    test('is opaque white at 100 percent', () => {
        expect(texel(100)).toEqual([255, 255, 255, 255]);
    });

    test('whitens and firms up steadily as the pack gets denser', () => {
        const levels = [20, 40, 60, 80, 100].map(texel);
        for (let c = 0; c < 4; c++) {
            const channel = levels.map((t) => t[c]);
            expect(channel).toEqual([...channel].sort((a, b) => a - b));   // never decreases
        }
    });
});

describe('formatDate', () => {
    test('shows the day as day, month and year', () => {
        expect(formatDate('2026-10-15')).toBe('15 Oct 2026');
        expect(formatDate('2026-01-02')).toBe('2 Jan 2026');
    });

    test('passes through a date it cannot read rather than hiding it', () => {
        expect(formatDate('latest')).toBe('latest');
    });
});

describe('legendLines', () => {
    test('names the layer, the day shown and the credit', () => {
        expect(legendLines({ sea_ice: { date: '2026-10-15' } })).toEqual({
            title: 'Sea ice concentration',
            date: 'Sea ice · 15 Oct 2026',
            credit: 'NOAA OISST',
        });
        expect(CREDIT).toBe('NOAA OISST');
    });

    test('still shows the layer and credit when the date is not available', () => {
        const lines = legendLines({});
        expect(lines.title).toBe('Sea ice concentration');
        expect(lines.date).toBeNull();
        expect(lines.credit).toBe('NOAA OISST');
    });
});

describe('legendGradient', () => {
    test('is a left-to-right CSS gradient from pale blue to white', () => {
        const css = legendGradient();
        expect(css.startsWith('linear-gradient(to right,')).toBe(true);
        const colours = css.match(/#[0-9a-f]{6}/gi);
        expect(colours.length).toBeGreaterThanOrEqual(2);
        expect(colours[colours.length - 1].toLowerCase()).toBe('#ffffff');
        expect(css.indexOf(colours[0])).toBeLessThan(css.indexOf(colours[colours.length - 1]));
    });
});
