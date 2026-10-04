import { describe, it, expect } from 'vitest';
import { logRange, densityTicks, formatDensity } from './population_density.js';

describe('population density scale', () => {
    it('spans log10 of the threshold to the colour scale top', () => {
        expect(logRange({ min_density: 10, max_density: 1000 })).toEqual([1, 3]);
    });

    it('falls back to the defaults and keeps a sliver of range when max <= min', () => {
        expect(logRange({})).toEqual([1, Math.log10(5000)]);
        const [lmin, lmax] = logRange({ min_density: 1000, max_density: 100 });
        expect(lmin).toBe(3);
        expect(lmax).toBeCloseTo(3.1);
    });

    it('ticks the ends and each power of ten between them', () => {
        const ticks = densityTicks({ min_density: 5, max_density: 5000 });
        expect(ticks.map((t) => Math.round(t))).toEqual([5, 10, 100, 1000, 5000]);
    });

    it('drops a power of ten crowding an end', () => {
        const ticks = densityTicks({ min_density: 10, max_density: 1000 });
        expect(ticks.map((t) => Math.round(t))).toEqual([10, 100, 1000]);
    });

    it('formats thousands compactly', () => {
        expect([5, 250, 1000, 2500, 50000].map(formatDensity)).toEqual(['5', '250', '1k', '2.5k', '50k']);
    });
});
