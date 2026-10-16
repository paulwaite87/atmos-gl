import { describe, expect, test } from 'vitest';
import {
    ATTRIBUTION, CAVEAT, affectedFilter, badgeSpec, fillColorExpression, fillOpacityExpression, formatDuration, legendItems,
    popupHtml, severityColor, severityLabel, signalLabel,
} from './internet_outages.js';

// One country entry as /api/internet_outages/countries serves it.
const LITHUANIA = {
    name: 'Lithuania',
    severity: 2,
    ongoing: true,
    signals: [
        { signal: 'gtr', start: '2026-10-10T05:00:00Z', duration_s: 6 * 86400 + 16 * 3600, ongoing: true },
        { signal: 'bgp', start: '2026-10-16T21:15:00Z', duration_s: 2 * 3600 + 5 * 60, ongoing: false },
    ],
};

describe('severityColor', () => {
    test('one signal is pale yellow, two orange, three red', () => {
        expect(severityColor(1)).toBe('#f7e463');
        expect(severityColor(2)).toBe('#f39c12');
        expect(severityColor(3)).toBe('#d62828');
    });

    test('anything above three is still red', () => {
        expect(severityColor(4)).toBe('#d62828');
    });
});

describe('severityLabel', () => {
    test('counts the signals that agree', () => {
        expect(severityLabel(1)).toBe('1 signal');
        expect(severityLabel(2)).toBe('2 signals');
        expect(severityLabel(3)).toBe('3 signals');
    });
});

describe('signalLabel', () => {
    test('names IODA signals in plain words and passes unknown ones through', () => {
        expect(signalLabel('bgp')).toBe('Routing (BGP)');
        expect(signalLabel('ping-slash24')).toBe('Active probing');
        expect(signalLabel('gtr')).toBe('Google Transparency Report');
        expect(signalLabel('merit-nt')).toBe('Network telescope');
        expect(signalLabel('new-thing')).toBe('new-thing');
    });
});

describe('formatDuration', () => {
    test('shows the two most useful units', () => {
        expect(formatDuration(45 * 60)).toBe('45m');
        expect(formatDuration(2 * 3600 + 5 * 60)).toBe('2h 5m');
        expect(formatDuration(6 * 86400 + 16 * 3600 + 120)).toBe('6d 16h');
    });
});

describe('fill expressions', () => {
    const countries = {
        LT: { ...LITHUANIA, severity: 2, ongoing: true },
        EG: { name: 'Egypt', severity: 1, ongoing: false, signals: [] },
        SY: { name: 'Syria', severity: 3, ongoing: true, signals: [] },
    };

    test('each affected country is coloured by its severity, keyed on its two-letter code', () => {
        expect(fillColorExpression(countries)).toEqual([
            'match', ['get', 'iso2'],
            'LT', '#f39c12',
            'EG', '#f7e463',
            'SY', '#d62828',
            'rgba(0,0,0,0)',
        ]);
    });

    test('ongoing outages are drawn strong and ended ones lighter', () => {
        expect(fillOpacityExpression(countries)).toEqual([
            'match', ['get', 'iso2'],
            'LT', 0.75,
            'EG', 0.3,
            'SY', 0.75,
            0,
        ]);
    });

    test('with nothing affected, nothing is filled (a match needs at least one pair)', () => {
        expect(fillColorExpression({})).toBe('rgba(0,0,0,0)');
        expect(fillOpacityExpression({})).toBe(0);
    });
});

describe('affectedFilter', () => {
    test('limits the layer to the affected countries, so hovering any other country does nothing', () => {
        expect(affectedFilter({ LT: LITHUANIA, EG: LITHUANIA })).toEqual(['in', ['get', 'iso2'], ['literal', ['LT', 'EG']]]);
    });

    test('matches nothing when no country is affected', () => {
        expect(affectedFilter({})).toEqual(['==', ['get', 'iso2'], '']);
    });
});

describe('popupHtml', () => {
    test('shows the country, severity, every signal with its start and ongoing/duration, the IODA link, credit and caveat', () => {
        const html = popupHtml('LT', LITHUANIA);
        expect(html).toContain('Lithuania');
        expect(html).toContain('2 signals');
        expect(html).toContain('Google Transparency Report');
        expect(html).toContain('10 Oct 2026 05:00 UTC');
        expect(html).toContain('ongoing 6d 16h');
        expect(html).toContain('Routing (BGP)');
        expect(html).toContain('16 Oct 2026 21:15 UTC');
        expect(html).toContain('lasted 2h 5m');
        expect(html).toContain('href="https://ioda.inetintel.cc.gatech.edu/country/LT"');
        expect(html).toContain(ATTRIBUTION);
        expect(html).toContain(CAVEAT);
        expect(ATTRIBUTION).toContain('IODA');
        expect(ATTRIBUTION).toContain('Georgia Tech');
        expect(CAVEAT).toMatch(/false positives/);
        expect(CAVEAT).toMatch(/not necessarily a shutdown/);
    });

    test('escapes feed text and only links to IODA for a plain two-letter code', () => {
        const hostile = { ...LITHUANIA, name: '<script>x</script>', signals: [{ ...LITHUANIA.signals[0], signal: '<b>y</b>' }] };
        const html = popupHtml('LT', hostile);
        expect(html).not.toContain('<script>');
        expect(html).not.toContain('<b>y</b>');
        expect(html).toContain('&lt;script&gt;');
        expect(popupHtml('"><x', LITHUANIA)).not.toContain('<a ');
    });
});

describe('legendItems', () => {
    test('explains the three colours and the lighter shade for outages that have ended', () => {
        const items = legendItems();
        expect(items.map((i) => i.color)).toEqual(['#f7e463', '#f39c12', '#d62828']);
        expect(items.map((i) => i.label)).toEqual(['1 signal', '2 signals', '3 signals']);
    });
});

describe('badgeSpec', () => {
    test('the badge carries the country\'s severity colour and an alert mark, ahead of the click', () => {
        expect(badgeSpec({ severity: 1 })).toEqual({ fill: '#f7e463', glyph: '!' });
        expect(badgeSpec({ severity: 2 })).toEqual({ fill: '#f39c12', glyph: '!' });
        expect(badgeSpec({ severity: 3 })).toEqual({ fill: '#d62828', glyph: '!' });
    });
});
