import { describe, it, expect } from 'vitest';
import {
    NO_DATA_COLOR, colorDomain, rampPosition, fillColorExpression, formatValue, withUnit,
    countryLine, attributionHtml, legendTicks, legendTitle,
} from './country_stats.js';

const LOG = { scale: 'log', colormap: 'viridis', title: 'Population', unit: 'people', short_unit: null };
const LINEAR = { scale: 'linear', colormap: 'magma', title: 'Life expectancy', unit: 'years', short_unit: 'years' };

// 101 countries valued 0..100 (linear), or 10^0..10^100 (log).
const spread = (toValue) => Object.fromEntries(
    Array.from({ length: 101 }, (_, i) => [`C${String(i).padStart(2, '0')}`, { year: 2023, value: toValue(i) }]));

describe('colour domain', () => {
    it('spans the 2nd to 98th percentile of a linear indicator', () => {
        expect(colorDomain(spread((i) => i), LINEAR)).toEqual([2, 98]);
    });

    it('works in log10 space for a log indicator', () => {
        const [lo, hi] = colorDomain(spread((i) => 10 ** i), LOG);
        expect(lo).toBeCloseTo(2);
        expect(hi).toBeCloseTo(98);
    });

    it('ignores stale countries and values a log scale cannot show', () => {
        const countries = {
            AAA: { year: 2023, value: 10 }, BBB: { year: 2023, value: 1000 },
            CCC: { year: 2010, stale: true }, DDD: { year: 2023, value: 0 },
        };
        const [lo, hi] = colorDomain(countries, LOG);
        expect(lo).toBeCloseTo(1.04);
        expect(hi).toBeCloseTo(2.96);
    });

    it('uses the catalog fixed domain when there is one', () => {
        expect(colorDomain(spread((i) => i), { ...LINEAR, domain: [40, 90] })).toEqual([40, 90]);
        expect(colorDomain({}, { ...LOG, domain: [10, 1e9] })).toEqual([1, 9]);
    });

    it('keeps a usable range with one value or none', () => {
        expect(colorDomain({ A: { year: 2023, value: 5 } }, LINEAR)).toEqual([4.5, 5.5]);
        expect(colorDomain({}, LINEAR)).toEqual([0, 1]);
    });
});

describe('ramp position', () => {
    it('clips values beyond the domain to its ends', () => {
        expect(rampPosition(50, LINEAR, [0, 100])).toBe(0.5);
        expect(rampPosition(-5, LINEAR, [0, 100])).toBe(0);
        expect(rampPosition(500, LINEAR, [0, 100])).toBe(1);
    });

    it('places log values by their power of ten', () => {
        expect(rampPosition(1000, LOG, [2, 4])).toBe(0.5);
        expect(rampPosition(0, LOG, [2, 4])).toBeNull();
    });
});

describe('fill colour expression', () => {
    it('colours each current country and leaves the rest no-data grey', () => {
        const expr = fillColorExpression({
            indicator: LINEAR,
            countries: { FRA: { year: 2023, value: 80 }, NOR: { year: 2010, stale: true }, AFG: { year: 2023, value: 60 } },
        });
        expect(expr.slice(0, 2)).toEqual(['match', ['get', 'code']]);
        expect(expr).toContain('FRA');
        expect(expr).toContain('AFG');
        expect(expr).not.toContain('NOR');
        expect(expr[expr.length - 1]).toBe(NO_DATA_COLOR);
        // AFG sits at the domain's bottom, FRA at its top: the ramp's two ends.
        expect(expr[expr.indexOf('AFG') + 1]).toBe('rgb(0,0,4)');
        expect(expr[expr.indexOf('FRA') + 1]).toBe('rgb(252,253,191)');
    });

    it('is a plain colour when nothing has data', () => {
        expect(fillColorExpression({ indicator: LINEAR, countries: {} })).toBe(NO_DATA_COLOR);
    });
});

describe('popup', () => {
    it('shows the value with its unit and year', () => {
        expect(countryLine({ year: 2023, value: 66438826 }, LOG)).toBe('Population: 66.4M people (2023)');
        expect(countryLine({ year: 2023, value: 82.536 }, LINEAR)).toBe('Life expectancy: 82.5 years (2023)');
    });

    it('says when there is no data, or only an old figure', () => {
        expect(countryLine(undefined, LOG)).toBe('No data');
        expect(countryLine({ year: 2010, stale: true }, LOG)).toBe('No recent data (latest 2010)');
    });

    it('puts currency symbols before the number', () => {
        expect(withUnit(45000, { short_unit: '$' })).toBe('$45,000');
        expect(withUnit(800.25, { short_unit: 'TWh' })).toBe('800 TWh');
    });

    it('credits the original source and OWID, escaping the citation', () => {
        const html = attributionHtml({ citation: 'UN WPP (2024) <b>' });
        expect(html).toContain('UN WPP (2024) &lt;b&gt;, via ');
        expect(html).toContain('ourworldindata.org');
        expect(attributionHtml({ citation: null })).toMatch(/^Via /);
    });
});

describe('legend', () => {
    it('ticks the ends and each power of ten between them on a log scale', () => {
        expect(legendTicks(LOG, [Math.log10(5), Math.log10(5000)]).map(Math.round)).toEqual([5, 10, 100, 1000, 5000]);
    });

    it('ticks the ends and midpoint on a linear scale', () => {
        expect(legendTicks(LINEAR, [50, 80])).toEqual([50, 65, 80]);
    });

    it('formats large numbers compactly', () => {
        expect([5, 82.5, 1234, 41454761, 8.09e9].map(formatValue)).toEqual(['5', '82.5', '1,234', '41.5M', '8.09B']);
    });

    it('titles the key with the indicator and its unit', () => {
        expect(legendTitle(LOG)).toBe('Population (people)');
        expect(legendTitle({ title: 'Index', unit: null, short_unit: null })).toBe('Index');
    });
});
