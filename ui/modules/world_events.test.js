import { describe, expect, test } from 'vitest';
import { alsoReportedByHtml, coincidentFeatures } from './world_events.js';

describe('alsoReportedByHtml', () => {
    test('is empty when there are no other outlets', () => {
        expect(alsoReportedByHtml([])).toBe('');
        expect(alsoReportedByHtml(undefined)).toBe('');
    });

    test('links each outlet by its domain, without www.', () => {
        const html = alsoReportedByHtml(['https://www.wutc.org/news/a', 'https://wysu.org/b']);
        expect(html).toBe(
            'Also reported by: '
            + '<a href="https://www.wutc.org/news/a" target="_blank" rel="noopener noreferrer">wutc.org</a>, '
            + '<a href="https://wysu.org/b" target="_blank" rel="noopener noreferrer">wysu.org</a>');
    });

    test('caps the list and says how many more', () => {
        const urls = Array.from({ length: 7 }, (_, i) => `https://outlet${i}.example/x`);
        const html = alsoReportedByHtml(urls);
        expect(html.match(/<a /g)).toHaveLength(5);
        expect(html.endsWith(' +2 more')).toBe(true);
    });

    test('escapes scraped URLs', () => {
        const html = alsoReportedByHtml(['https://evil.example/"><script>x</script>']);
        expect(html).not.toContain('<script>');
        expect(html).toContain('&quot;&gt;&lt;script&gt;');
    });
});

describe('coincidentFeatures', () => {
    const at = (id, lon, lat) => ({ properties: { id }, geometry: { coordinates: [lon, lat] } });

    test('is just the hovered feature when nothing shares its point', () => {
        const top = at('a', 174.78, -41.3);
        expect(coincidentFeatures(top, [top, at('b', 174.58, -36.75)])).toEqual([top]);
    });

    test('lists every story stacked on the same point, hovered one first, once each', () => {
        const top = at('a', 174.78, -41.3);
        const b = at('b', 174.78, -41.3);
        const c = at('c', 174.78, -41.3);
        // e.features repeats a feature when tiles overlap
        expect(coincidentFeatures(top, [top, b, at('d', 0, 0), c, b]).map((f) => f.properties.id))
            .toEqual(['a', 'b', 'c']);
    });
});
