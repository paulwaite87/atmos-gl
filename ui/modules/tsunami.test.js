import { describe, expect, test } from 'vitest';
import {
    ATTRIBUTION, DISCLAIMER, badgeImageId, badgeSpec, createFootprintSelector, popupHtml,
    selectionFilter, styleFootprint, zoneColor,
} from './tsunami.js';

// Properties as the /api/tsunami/geojson route serves them for the recorded Kamchatka quake.
const KAMCHATKA = {
    centre: 'PAAQ',
    event_id: 'tmzdyc',
    category: 'Information',
    bulletin_number: 1,
    location_name: '415 miles SW of Bering I., Komandorski',
    magnitude: 7.0,
    magnitude_type: 'Mwp',
    depth_km: 148,
    origin_time: '2026-10-16T03:35:31Z',
    expires: '2026-10-16T04:47:16Z',
    bulletin_url: 'http://www.tsunami.gov/events/PAAQ/2026/10/16/tmzdyc/1/WEAK53/',
    note: 'Based on the depth of the earthquake, a tsunami is not expected.',
};

describe('badgeSpec', () => {
    test('category sets colour AND silhouette, so severity is not colour-only', () => {
        expect(badgeSpec('Warning')).toEqual({ shape: 'octagon', fill: '#d62828' });
        expect(badgeSpec('Watch')).toEqual({ shape: 'diamond', fill: '#f39c12' });
        expect(badgeSpec('Advisory')).toEqual({ shape: 'triangle', fill: '#f1c40f' });
        expect(badgeSpec('Information')).toEqual({ shape: 'circle', fill: '#7f8c8d' });
    });

    test('an unrecognised category still gets a neutral badge', () => {
        expect(badgeSpec('Mystery')).toEqual({ shape: 'circle', fill: '#7f8c8d' });
    });
});

describe('badgeImageId', () => {
    test('is unique per category and distinct from the GDACS badge ids', () => {
        expect(badgeImageId('Warning')).toBe('tsunami-Warning');
        expect(badgeImageId('Watch')).not.toBe(badgeImageId('Warning'));
        expect(badgeImageId('Warning')).not.toMatch(/^gdacs-/);
    });
});

describe('zoneColor', () => {
    test('shaded zones reuse the badge colour of their category', () => {
        expect(zoneColor('Warning')).toBe('#d62828');
        expect(zoneColor('Watch')).toBe('#f39c12');
        expect(zoneColor('Advisory')).toBe('#f1c40f');
    });
});

describe('popupHtml', () => {
    test('shows category, bulletin, location, magnitude, depth, times, note, link and the disclaimer', () => {
        const html = popupHtml({ properties: KAMCHATKA });
        expect(html).toContain('415 miles SW of Bering I., Komandorski');
        expect(html).toContain('Information');
        expect(html).toContain('Bulletin 1');
        expect(html).toContain('Mwp 7.0');
        expect(html).toContain('148 km');
        expect(html).toContain('16 Oct 2026 03:35 UTC');
        expect(html).toContain('16 Oct 2026 04:47 UTC');
        expect(html).toContain('a tsunami is not expected');
        expect(html).toContain('href="https://www.tsunami.gov/events/PAAQ/2026/10/16/tmzdyc/1/WEAK53/"');
        expect(html).toContain(ATTRIBUTION);
        expect(html).toContain(DISCLAIMER);
        expect(ATTRIBUTION).toContain('NOAA');
        expect(DISCLAIMER).toMatch(/not an official/i);
    });

    test('leaves out rows the centre gave no value for', () => {
        const html = popupHtml({ properties: { ...KAMCHATKA, note: null, depth_km: null } });
        expect(html).not.toContain('Depth');
        expect(html).not.toContain('Note');
        expect(html).toContain('Mwp 7.0');
    });

    test('a long note is cut short so a warning popup stays readable', () => {
        const html = popupHtml({ properties: { ...KAMCHATKA, note: 'word '.repeat(200) } });
        expect(html.length).toBeLessThan(3000);
        expect(html).toContain('…');
    });

    test('escapes feed text and only links bulletins hosted on tsunami.gov', () => {
        const html = popupHtml({
            properties: { ...KAMCHATKA, location_name: '<script>x</script>', bulletin_url: 'https://evil.example/tsunami.gov/' },
        });
        expect(html).not.toContain('<script>');
        expect(html).toContain('&lt;script&gt;');
        expect(html).not.toContain('evil.example');
        expect(html).not.toContain('<a ');
    });
});

describe('styleFootprint', () => {
    const collection = {
        type: 'FeatureCollection',
        features: [
            { type: 'Feature', geometry: { type: 'Polygon', coordinates: [] }, properties: { zone_code: 'CAZ042', category: 'Warning' } },
            { type: 'Feature', geometry: { type: 'MultiPolygon', coordinates: [] }, properties: { zone_code: 'PZZ530', category: 'Advisory' } },
        ],
    };

    test('stamps each zone with the colour of its category', () => {
        const styled = styleFootprint(collection);
        expect(styled.features.map((f) => f.properties.color)).toEqual(['#d62828', '#f1c40f']);
    });

    test('does not mutate what it was given', () => {
        styleFootprint(collection);
        expect(collection.features[0].properties).toEqual({ zone_code: 'CAZ042', category: 'Warning' });
    });
});

describe('selectionFilter', () => {
    test('matches exactly the selected event on the markers source', () => {
        expect(selectionFilter({ centre: 'PAAQ', event_id: 'tmzdyc' })).toEqual([
            'all',
            ['==', ['get', 'centre'], 'PAAQ'],
            ['==', ['get', 'event_id'], 'tmzdyc'],
        ]);
    });

    test('matches nothing when no event is selected', () => {
        expect(selectionFilter(null)).toEqual(['==', ['get', 'event_id'], '']);
    });
});

describe('createFootprintSelector', () => {
    const footprint = (category) => ({
        type: 'FeatureCollection',
        features: [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: [] }, properties: { zone_code: 'Z', category } }],
    });

    function make(responses) {
        const drawn = [];
        const urls = [];
        const selector = createFootprintSelector({
            apiBase: '/api',
            fetchJson: (url) => { urls.push(url); return responses[url]; },
            setData: (data) => drawn.push(data),
        });
        return { selector, drawn, urls };
    }

    test('selecting an event fetches its zones from our own API and draws them styled', async () => {
        const { selector, drawn, urls } = make({ '/api/tsunami/PAAQ/maiqdx/footprint': Promise.resolve(footprint('Warning')) });
        await selector.select({ centre: 'PAAQ', event_id: 'maiqdx' });
        expect(urls).toEqual(['/api/tsunami/PAAQ/maiqdx/footprint']);
        expect(drawn).toHaveLength(1);
        expect(drawn[0].features[0].properties.color).toBe('#d62828');
    });

    test('selecting another event replaces the previous footprint', async () => {
        const { selector, drawn } = make({
            '/api/tsunami/PAAQ/a/footprint': Promise.resolve(footprint('Warning')),
            '/api/tsunami/PHEB/b/footprint': Promise.resolve(footprint('Advisory')),
        });
        await selector.select({ centre: 'PAAQ', event_id: 'a' });
        await selector.select({ centre: 'PHEB', event_id: 'b' });
        expect(drawn).toHaveLength(2);
        expect(drawn[1].features[0].properties.category).toBe('Advisory');
    });

    test('clear() empties the map; a slow earlier response cannot redraw afterwards', async () => {
        let release;
        const slow = new Promise((resolve) => { release = () => resolve(footprint('Warning')); });
        const { selector, drawn } = make({ '/api/tsunami/PAAQ/a/footprint': slow });
        const pending = selector.select({ centre: 'PAAQ', event_id: 'a' });
        selector.clear();
        release();
        await pending;
        expect(drawn).toHaveLength(1);
        expect(drawn[0].features).toEqual([]);
    });

    test('a failed fetch leaves the current drawing untouched', async () => {
        const { selector, drawn } = make({ '/api/tsunami/PAAQ/a/footprint': Promise.reject(new Error('HTTP 500')) });
        await selector.select({ centre: 'PAAQ', event_id: 'a' });
        expect(drawn).toEqual([]);
    });

    describe('reporting the selection (for the highlight ring)', () => {
        function makeReporting(responses) {
            const events = [];
            const selector = createFootprintSelector({
                apiBase: '/api',
                fetchJson: (url) => responses[url],
                setData: (data) => events.push(data.features.length ? 'draw' : 'erase'),
                onSelect: (sel) => events.push(sel ? `select:${sel.centre}/${sel.event_id}` : 'select:none'),
            });
            return { selector, events };
        }

        test('reports the selection once its footprint is drawn, and none when cleared', async () => {
            const { selector, events } = makeReporting({ '/api/tsunami/PAAQ/a/footprint': Promise.resolve(footprint('Warning')) });
            await selector.select({ centre: 'PAAQ', event_id: 'a', category: 'ignored' });
            selector.clear();
            expect(events).toEqual(['draw', 'select:PAAQ/a', 'erase', 'select:none']);
        });

        test('an event with no zones is still reported as selected', async () => {
            const { selector, events } = makeReporting({
                '/api/tsunami/PHEB/k/footprint': Promise.resolve({ type: 'FeatureCollection', features: [] }),
            });
            await selector.select({ centre: 'PHEB', event_id: 'k' });
            expect(events).toEqual(['erase', 'select:PHEB/k']);
        });

        test('a failed fetch leaves the previously reported selection standing', async () => {
            const { selector, events } = makeReporting({
                '/api/tsunami/PAAQ/a/footprint': Promise.resolve(footprint('Warning')),
                '/api/tsunami/PAAQ/b/footprint': Promise.reject(new Error('HTTP 500')),
            });
            await selector.select({ centre: 'PAAQ', event_id: 'a' });
            await selector.select({ centre: 'PAAQ', event_id: 'b' });
            expect(events).toEqual(['draw', 'select:PAAQ/a']);
        });

        test('a superseded slow response reports nothing', async () => {
            let release;
            const slow = new Promise((resolve) => { release = () => resolve(footprint('Warning')); });
            const { selector, events } = makeReporting({
                '/api/tsunami/PAAQ/a/footprint': slow,
                '/api/tsunami/PAAQ/b/footprint': Promise.resolve(footprint('Advisory')),
            });
            const first = selector.select({ centre: 'PAAQ', event_id: 'a' });
            await selector.select({ centre: 'PAAQ', event_id: 'b' });
            release();
            await first;
            expect(events).toEqual(['draw', 'select:PAAQ/b']);
        });
    });
});
