import { describe, expect, test } from 'vitest';
import {
    ATTRIBUTION, badgeImageId, badgeSpec, classColor, createFootprintSelector, popupHtml, selectionFilter,
    styleFootprint,
} from './gdacs.js';

// Properties as the /api/gdacs/geojson route serves them for the recorded Red cyclone.
const CHAMPI = {
    event_type: 'TC',
    event_id: 1001338,
    name: 'Tropical Cyclone CHAMPI-26',
    alert_level: 'Red',
    alert_score: 3,
    severity_text: 'Tropical Storm (maximum wind speed of 241 km/h)',
    country: 'Guam',
    from_date: '2026-10-14T12:00:00Z',
    to_date: '2026-10-16T00:00:00Z',
    is_current: true,
    report_url: 'https://www.gdacs.org/report.aspx?eventid=1001338&episodeid=7&eventtype=TC',
};

describe('badgeSpec', () => {
    test('alert level sets colour AND silhouette, so severity is not colour-only', () => {
        expect(badgeSpec('FL', 'Green')).toEqual({ shape: 'circle', fill: '#2e9e5b', code: 'FL' });
        expect(badgeSpec('EQ', 'Orange')).toEqual({ shape: 'diamond', fill: '#f39c12', code: 'EQ' });
        expect(badgeSpec('TC', 'Red')).toEqual({ shape: 'octagon', fill: '#d62828', code: 'TC' });
    });

    test('an unrecognised alert level still gets a neutral badge', () => {
        expect(badgeSpec('XX', 'Purple')).toEqual({ shape: 'circle', fill: '#888888', code: 'XX' });
    });
});

describe('badgeImageId', () => {
    test('is unique per type and alert level', () => {
        expect(badgeImageId('TC', 'Red')).toBe('gdacs-TC-Red');
        expect(badgeImageId('TC', 'Orange')).not.toBe(badgeImageId('TC', 'Red'));
        expect(badgeImageId('FL', 'Red')).not.toBe(badgeImageId('TC', 'Red'));
    });
});

describe('popupHtml', () => {
    test('shows name, alert, severity, country, period, report link and the attribution', () => {
        const html = popupHtml({ properties: CHAMPI });
        expect(html).toContain('Tropical Cyclone CHAMPI-26');
        expect(html).toContain('Red');
        expect(html).toContain('Tropical Storm (maximum wind speed of 241 km/h)');
        expect(html).toContain('Guam');
        expect(html).toContain('14 Oct 2026');
        expect(html).toContain('ongoing');
        expect(html).toContain(
            'href="https://www.gdacs.org/report.aspx?eventid=1001338&amp;episodeid=7&amp;eventtype=TC"');
        expect(html).toContain(
            'Source: GDACS (European Commission JRC) · indicative only · geometry simplified');
        expect(ATTRIBUTION).toBe(
            'Source: GDACS (European Commission JRC) · indicative only · geometry simplified');
    });

    test('an ended event shows its end date instead of "ongoing"', () => {
        const html = popupHtml({ properties: { ...CHAMPI, is_current: false } });
        expect(html).toContain('14 Oct 2026');
        expect(html).toContain('16 Oct 2026');
        expect(html).not.toContain('ongoing');
    });

    test('leaves out rows GDACS gave no value for (e.g. a volcano has no severity text)', () => {
        const html = popupHtml({ properties: { ...CHAMPI, severity_text: '' } });
        expect(html).not.toContain('Severity');
        expect(html).toContain('Guam');
    });

    test('escapes feed text and only links reports hosted on gdacs.org', () => {
        const html = popupHtml({
            properties: { ...CHAMPI, name: '<script>x</script>', report_url: 'javascript:alert(1)' },
        });
        expect(html).not.toContain('<script>');
        expect(html).toContain('&lt;script&gt;');
        expect(html).not.toContain('javascript:');
        expect(html).not.toContain('<a ');
    });
});

describe('classColor', () => {
    test('GDACS impact-band polygons reuse the alert colours', () => {
        expect(classColor('Poly_Green')).toBe('#2e9e5b');
        expect(classColor('Poly_Orange')).toBe('#f39c12');
        expect(classColor('Poly_Red')).toBe('#d62828');
    });

    test('tracks are white and an unknown class falls back to neutral grey', () => {
        expect(classColor('Line_Line_3')).toBe('#ffffff');
        expect(classColor('Something_New')).toBe('#888888');
    });
});

describe('styleFootprint', () => {
    const collection = {
        type: 'FeatureCollection',
        features: [
            { type: 'Feature', geometry: { type: 'Polygon', coordinates: [] }, properties: { feature_class: 'Poly_Red' } },
            { type: 'Feature', geometry: { type: 'MultiPolygon', coordinates: [] }, properties: { feature_class: 'Poly_Affected' } },
            { type: 'Feature', geometry: { type: 'LineString', coordinates: [] }, properties: { feature_class: 'Line_Line_0' } },
        ],
    };

    test('stamps each feature with its colour and whether it draws as a fill or a line', () => {
        const styled = styleFootprint(collection);
        expect(styled.features.map((f) => f.properties.kind)).toEqual(['fill', 'fill', 'line']);
        expect(styled.features[0].properties.color).toBe('#d62828');
        expect(styled.features[2].properties.color).toBe('#ffffff');
    });

    test('does not mutate what it was given', () => {
        styleFootprint(collection);
        expect(collection.features[0].properties).toEqual({ feature_class: 'Poly_Red' });
    });
});

describe('selectionFilter', () => {
    test('matches exactly the selected event on the markers source', () => {
        expect(selectionFilter({ event_type: 'TC', event_id: 1001338 })).toEqual([
            'all',
            ['==', ['get', 'event_type'], 'TC'],
            ['==', ['get', 'event_id'], 1001338],
        ]);
    });

    test('matches nothing when no event is selected', () => {
        expect(selectionFilter(null)).toEqual(['==', ['get', 'event_id'], -1]);
    });
});

describe('createFootprintSelector', () => {
    const footprint = (cls) => ({
        type: 'FeatureCollection',
        features: [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: [] }, properties: { feature_class: cls } }],
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

    test('selecting an event fetches its footprint from our own API and draws it styled', async () => {
        const { selector, drawn, urls } = make({ '/api/gdacs/TC/1001338/footprint': Promise.resolve(footprint('Poly_Red')) });
        await selector.select({ event_type: 'TC', event_id: 1001338 });
        expect(urls).toEqual(['/api/gdacs/TC/1001338/footprint']);
        expect(drawn).toHaveLength(1);
        expect(drawn[0].features[0].properties.color).toBe('#d62828');
    });

    test('selecting another event replaces the previous footprint', async () => {
        const { selector, drawn } = make({
            '/api/gdacs/TC/1/footprint': Promise.resolve(footprint('Poly_Red')),
            '/api/gdacs/FL/2/footprint': Promise.resolve(footprint('Poly_Affected')),
        });
        await selector.select({ event_type: 'TC', event_id: 1 });
        await selector.select({ event_type: 'FL', event_id: 2 });
        expect(drawn).toHaveLength(2);
        expect(drawn[1].features[0].properties.feature_class).toBe('Poly_Affected');
    });

    test('clear() empties the map; a slow earlier response cannot redraw afterwards', async () => {
        let release;
        const slow = new Promise((resolve) => { release = () => resolve(footprint('Poly_Red')); });
        const { selector, drawn } = make({ '/api/gdacs/TC/1/footprint': slow });
        const pending = selector.select({ event_type: 'TC', event_id: 1 });
        selector.clear();
        release();
        await pending;
        expect(drawn).toHaveLength(1);
        expect(drawn[0].features).toEqual([]);
    });

    test('a failed fetch leaves the current drawing untouched', async () => {
        const { selector, drawn } = make({ '/api/gdacs/TC/9/footprint': Promise.reject(new Error('HTTP 500')) });
        await selector.select({ event_type: 'TC', event_id: 9 });
        expect(drawn).toEqual([]);
    });

    describe('reporting the selection (for the highlight ring)', () => {
        function makeReporting(responses) {
            const events = [];
            const selector = createFootprintSelector({
                apiBase: '/api',
                fetchJson: (url) => responses[url],
                setData: (data) => events.push(data.features.length ? 'draw' : 'erase'),
                onSelect: (sel) => events.push(sel ? `select:${sel.event_type}/${sel.event_id}` : 'select:none'),
            });
            return { selector, events };
        }

        test('reports the selection once its footprint is drawn, and none when cleared', async () => {
            const { selector, events } = makeReporting({ '/api/gdacs/TC/1/footprint': Promise.resolve(footprint('Poly_Red')) });
            await selector.select({ event_type: 'TC', event_id: 1, name: 'ignored' });
            selector.clear();
            expect(events).toEqual(['draw', 'select:TC/1', 'erase', 'select:none']);
        });

        test('a failed fetch leaves the previously reported selection standing', async () => {
            const { selector, events } = makeReporting({
                '/api/gdacs/TC/1/footprint': Promise.resolve(footprint('Poly_Red')),
                '/api/gdacs/FL/2/footprint': Promise.reject(new Error('HTTP 500')),
            });
            await selector.select({ event_type: 'TC', event_id: 1 });
            await selector.select({ event_type: 'FL', event_id: 2 });
            expect(events).toEqual(['draw', 'select:TC/1']);
        });

        test('a superseded slow response reports nothing', async () => {
            let release;
            const slow = new Promise((resolve) => { release = () => resolve(footprint('Poly_Red')); });
            const { selector, events } = makeReporting({
                '/api/gdacs/TC/1/footprint': slow,
                '/api/gdacs/FL/2/footprint': Promise.resolve(footprint('Poly_Affected')),
            });
            const first = selector.select({ event_type: 'TC', event_id: 1 });
            await selector.select({ event_type: 'FL', event_id: 2 });
            release();
            await first;
            expect(events).toEqual(['draw', 'select:FL/2']);
        });
    });
});
