// Tests for the shared fetch/icon/popup-card plumbing behind the six event-feed
// layers (architecture review candidate "six frontend event-feed modules copy-paste
// the same load scaffold"). fetch/window/createImageBitmap are faked minimally, same
// approach _reconcile.test.js/_legend.test.js take for browser globals.
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fetchOrThrow, preloadIcons, popupCard, escapeHtml, buildPopupHtml } from './_feedhelpers.js';

function fakeMap(existingIds = []) {
    const images = new Set(existingIds);
    return {
        hasImage: vi.fn((id) => images.has(id)),
        addImage: vi.fn((id) => { images.add(id); }),
    };
}

beforeEach(() => {
    globalThis.window = { location: { origin: 'http://test' } };
    globalThis.createImageBitmap = vi.fn(async () => 'bitmap');
});

describe('fetchOrThrow', () => {
    test('returns the parsed JSON body on a 200', async () => {
        globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ a: 1 }) }));
        await expect(fetchOrThrow('http://test/x')).resolves.toEqual({ a: 1 });
    });

    test('throws with the HTTP status when the response is not ok', async () => {
        globalThis.fetch = vi.fn(async () => ({ ok: false, status: 503 }));
        await expect(fetchOrThrow('http://test/x')).rejects.toThrow('HTTP 503');
    });
});

describe('preloadIcons', () => {
    test('skips icons the map already has', async () => {
        const map = fakeMap(['icon-a']);
        globalThis.fetch = vi.fn();
        await preloadIcons(map, [{ id: 'icon-a', url: '/images/a.png' }]);
        expect(globalThis.fetch).not.toHaveBeenCalled();
        expect(map.addImage).not.toHaveBeenCalled();
    });

    test('fetches and adds missing icons, resolving the url against window.location.origin', async () => {
        const map = fakeMap();
        globalThis.fetch = vi.fn(async (url) => ({ ok: true, blob: async () => `blob:${url}` }));
        await preloadIcons(map, [{ id: 'icon-a', url: '/images/a.png' }]);

        expect(globalThis.fetch).toHaveBeenCalledWith('http://test/images/a.png');
        expect(map.addImage).toHaveBeenCalledWith('icon-a', 'bitmap');
    });

    test('throws when an icon fails to load', async () => {
        const map = fakeMap();
        globalThis.fetch = vi.fn(async () => ({ ok: false }));
        await expect(preloadIcons(map, [{ id: 'icon-a', url: '/images/a.png' }]))
            .rejects.toThrow('Could not load icon-a');
    });

    test('registers an icon marked sdf:true with the sdf option, for render-time tinting', async () => {
        const map = fakeMap();
        globalThis.fetch = vi.fn(async () => ({ ok: true, blob: async () => 'blob' }));
        await preloadIcons(map, [{ id: 'icon-a', url: '/images/a.png', sdf: true }]);
        expect(map.addImage).toHaveBeenCalledWith('icon-a', 'bitmap', { sdf: true });
    });

    test('loads multiple missing icons in parallel, leaving present ones untouched', async () => {
        const map = fakeMap(['icon-b']);
        globalThis.fetch = vi.fn(async () => ({ ok: true, blob: async () => 'blob' }));
        await preloadIcons(map, [
            { id: 'icon-a', url: '/images/a.png' },
            { id: 'icon-b', url: '/images/b.png' },
        ]);

        expect(globalThis.fetch).toHaveBeenCalledTimes(1);
        expect(map.addImage).toHaveBeenCalledTimes(1);
        expect(map.addImage).toHaveBeenCalledWith('icon-a', 'bitmap');
    });
});

describe('escapeHtml', () => {
    test('escapes &, <, >, ", and \'', () => {
        expect(escapeHtml(`<img src=x onerror=alert(1)> & "quotes" 'n stuff`))
            .toBe('&lt;img src=x onerror=alert(1)&gt; &amp; &quot;quotes&quot; &#39;n stuff');
    });

    test('a plain string round-trips unchanged', () => {
        expect(escapeHtml('Colombia')).toBe('Colombia');
    });

    test('null/undefined become an empty string, not the literal word', () => {
        expect(escapeHtml(null)).toBe('');
        expect(escapeHtml(undefined)).toBe('');
    });

    test('non-string values are stringified first', () => {
        expect(escapeHtml(4)).toBe('4');
    });
});

describe('popupCard', () => {
    test('escapes an XSS payload in the title and in a row value, so it renders inert', () => {
        const html = popupCard({
            title: '<script>alert(1)</script>',
            rows: [{ label: 'Name', value: '<img src=x onerror=alert(1)>' }],
        });
        expect(html).not.toContain('<script>');
        expect(html).not.toContain('<img src=x onerror=alert(1)>');
        expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
        expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
    });


    test('renders the title, hr, and each row (bold label, grey value) with default width', () => {
        const html = popupCard({
            title: 'Test Volcano',
            rows: [{ label: 'VEI', value: 4 }],
        });
        expect(html).toContain('Test Volcano');
        expect(html).toContain('<hr');
        expect(html).toContain('<strong style="min-width:45px;display:inline-block;margin-right:6px;">VEI:</strong>');
        expect(html).toContain('<span style="color:#666;">4</span>');
    });

    test('applies per-row width and card-level title/padding overrides', () => {
        const html = popupCard({
            title: 'Satellite',
            titleColor: '#222',
            titleSize: 14,
            padding: 4,
            rows: [{ label: 'NORAD', value: 123, width: 50 }],
        });
        expect(html).toContain('font-size:14px;color:#222');
        expect(html).toContain('padding:4px');
        expect(html).toContain('width:50px');
    });

    test('renders no rows when the rows array is empty', () => {
        const html = popupCard({ title: 'Empty' });
        expect(html).toContain('Empty');
        expect(html).not.toContain('<span');
    });

    test('defaults the body font-size to 12px', () => {
        const html = popupCard({ title: 'Test' });
        expect(html).toContain('font-size:12px;color:#000');
    });

    test('applies an explicit fontSize to the body', () => {
        const html = popupCard({ title: 'Test', fontSize: 16 });
        expect(html).toContain('font-size:16px;color:#000');
    });
});

// buildPopupHtml -- the one-stop-shop content model replacing popupCard AND every
// hand-rolled popup template (architecture review candidate #6, superseding
// docs/adr/0002-dont-extend-hoverpopup-for-markers.md). Grounded directly against
// each of the 8 real call sites' current output shape so migrating a caller can be
// checked byte-for-byte against these fixtures.
describe('buildPopupHtml', () => {
    describe('title', () => {
        test('escapes the title text by default', () => {
            const html = buildPopupHtml({ title: { text: '<script>x</script>' } });
            expect(html).not.toContain('<script>');
            expect(html).toContain('&lt;script&gt;x&lt;/script&gt;');
        });

        test('the default variant matches #333/13px (was popupCard\'s own default)', () => {
            const html = buildPopupHtml({ title: { text: 'Satellite' } });
            expect(html).toContain('<strong style="font-size:13px;color:#333;">Satellite</strong>');
        });

        test('the callsign variant is #007bff/16px (flightradar/volcanoes/shipping)', () => {
            const html = buildPopupHtml({ title: { text: 'BAW123', variant: 'callsign' } });
            expect(html).toContain('<strong style="font-size:16px;color:#007bff;">BAW123</strong>');
        });

        test('the alert variant is #ff4a4a/14px (storms/lightning/quakes)', () => {
            const html = buildPopupHtml({ title: { text: 'Cyclone Freddy', variant: 'alert' } });
            expect(html).toContain('<strong style="font-size:14px;color:#ff4a4a;">Cyclone Freddy</strong>');
        });

        test('the plain variant is #000/14px (markers -- bold, no accent colour)', () => {
            const html = buildPopupHtml({ title: { text: 'Auckland', variant: 'plain' } });
            expect(html).toContain('<strong style="font-size:14px;color:#000;">Auckland</strong>');
        });

        test('an unknown variant falls back to default rather than throwing', () => {
            const html = buildPopupHtml({ title: { text: 'X', variant: 'not-a-variant' } });
            expect(html).toContain('font-size:13px;color:#333;');
        });

        test('suffix appends pre-built HTML on the same line, unescaped (quakes\' fused "M 5.2 — Place" line)', () => {
            const html = buildPopupHtml({
                title: { text: 'M 5.2', variant: 'alert', suffix: ` — ${escapeHtml('Ridgecrest, CA')}` },
            });
            expect(html).toContain(
                '<strong style="font-size:14px;color:#ff4a4a;">M 5.2</strong> — Ridgecrest, CA');
        });
    });

    describe('subtitle', () => {
        test('renders nothing when omitted', () => {
            const html = buildPopupHtml({ title: { text: 'X' } });
            expect(html).not.toContain('margin-top:-2px');
        });

        test('renders pre-built HTML under the title (markers\' country/pop line)', () => {
            const html = buildPopupHtml({
                title: { text: 'Auckland' },
                subtitle: `${escapeHtml('New Zealand')}<br/>Pop: 1,657,000`,
            });
            expect(html).toContain(
                '<div style="color:#888;font-size:11px;margin-top:-2px;">New Zealand<br/>Pop: 1,657,000</div>');
        });
    });

    describe('divider block', () => {
        test('renders one canonical hr style, regardless of caller', () => {
            const html = buildPopupHtml({ title: { text: 'X' }, blocks: [{ type: 'divider' }] });
            expect(html).toContain('<hr style="border:0;border-top:1px solid #ccc;margin:4px 0;">');
        });
    });

    describe('rows block', () => {
        test('escapes an XSS payload in a row label and value', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'rows', rows: [{ label: '<b>hax</b>', value: '<img src=x onerror=alert(1)>' }] }],
            });
            expect(html).not.toContain('<img src=x onerror=alert(1)>');
            expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
        });

        test('bold label, grey value, default width 45 (matches popupCard exactly)', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'rows', rows: [{ label: 'VEI', value: 4 }] }],
            });
            expect(html).toContain(
                '<strong style="min-width:45px;display:inline-block;margin-right:6px;">VEI:</strong>');
            expect(html).toContain('<span style="color:#666;">4</span>');
        });

        test('honours a per-row width override (storms\' long labels)', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'rows', rows: [{ label: 'Storm category', value: 'Hurricane', width: 130 }] }],
            });
            expect(html).toContain('min-width:130px');
        });

        test('honours a per-row computed valueColor (lightning\'s age-based colour)', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'rows', rows: [{ label: 'Age', value: '3 mins ago', valueColor: '#28a745' }] }],
            });
            expect(html).toContain('<span style="color:#28a745;">3 mins ago</span>');
        });
    });

    describe('line block', () => {
        test('escapes label and value by default', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'line', items: [{ label: 'Registration', value: '<script>x</script>' }] }],
            });
            expect(html).not.toContain('<script>');
        });

        test('single item renders as its own block-level "Label: value" line', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'line', items: [{ label: 'Type', value: 'A320' }] }],
            });
            expect(html).toContain('<div><span style="color:#666;">Type:</span> A320</div>');
        });

        test('multiple items on one line are joined with " | " (shipping\'s MMSI | IMO)', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'line', items: [
                    { label: 'MMSI', value: '123456789' },
                    { label: 'IMO', value: '987654' },
                ] }],
            });
            expect(html).toContain(
                '<div><span style="color:#666;">MMSI:</span> 123456789 | <span style="color:#666;">IMO:</span> 987654</div>');
        });

        test('a line always starts on its own line, immediately after the title (no explicit divider needed)', () => {
            const html = buildPopupHtml({
                title: { text: 'BAW123', variant: 'callsign' },
                blocks: [{ type: 'line', items: [{ label: 'Type', value: 'A320' }] }],
            });
            expect(html).toContain('</strong><div><span style="color:#666;">Type:</span> A320</div>');
        });

        test('raw:true skips escaping (flight radar\'s &deg; heading entity)', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'line', items: [{ label: 'Heading', value: '270&deg;', raw: true }] }],
            });
            expect(html).toContain('270&deg;');
            expect(html).not.toContain('&amp;deg;');
        });
    });

    describe('emphasis block', () => {
        test('wraps pre-built HTML in the bold/20px style (flight radar\'s route block)', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'emphasis', html: 'LHR &rarr; JFK' }],
            });
            expect(html).toContain(
                '<div style="font-weight:bold;color:#000;font-size:20px;margin-top:2px;">LHR &rarr; JFK</div>');
        });
    });

    describe('notice block', () => {
        test('defaults to the stale-signal warning colour, escaping text by default', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'notice', text: '<b>hax</b>' }],
            });
            expect(html).toContain('color:#c0392b');
            expect(html).not.toContain('<b>hax</b>');
        });

        test('raw:true allows an HTML entity through unescaped (the warning triangle)', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'notice', text: '&#9888; Signal lost -- position frozen', raw: true }],
            });
            expect(html).toContain('&#9888; Signal lost -- position frozen');
        });

        test('an explicit color overrides the default', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'notice', text: 'note', color: '#f0ad4e' }],
            });
            expect(html).toContain('color:#f0ad4e');
        });
    });

    describe('fallback block', () => {
        test('renders a single muted line, escaped', () => {
            const html = buildPopupHtml({
                title: { text: 'X' },
                blocks: [{ type: 'fallback', text: 'Weather data unavailable' }],
            });
            expect(html).toContain('<div style="color:#888;">Weather data unavailable</div>');
        });
    });

    describe('unknown block type', () => {
        test('throws rather than silently rendering nothing', () => {
            expect(() => buildPopupHtml({ title: { text: 'X' }, blocks: [{ type: 'bogus' }] }))
                .toThrow(/unknown block type/);
        });
    });

    describe('wrapper', () => {
        test('defaults to padding:5px, font-size:12px (matches the 4 hand-rolled templates)', () => {
            const html = buildPopupHtml({ title: { text: 'X' } });
            expect(html).toContain('font-family:sans-serif;font-size:12px;color:#000;padding:5px;');
        });

        test('padding is a per-call override (volcanoes uses 3px)', () => {
            const html = buildPopupHtml({ title: { text: 'X' }, padding: 3 });
            expect(html).toContain('padding:3px;');
        });

        test('fontSize is a per-call override (storms reads the live popup_fontsize setting)', () => {
            const html = buildPopupHtml({ title: { text: 'X' }, fontSize: 16 });
            expect(html).toContain('font-size:16px;color:#000');
        });
    });

    test('composes title + subtitle + multiple blocks in order', () => {
        const html = buildPopupHtml({
            title: { text: 'BAW123', variant: 'callsign' },
            blocks: [
                { type: 'emphasis', html: 'LHR &rarr; JFK' },
                { type: 'divider' },
                { type: 'line', items: [{ label: 'Type', value: 'A320' }] },
                { type: 'notice', text: 'Signal lost', color: '#c0392b' },
            ],
        });
        const order = [
            html.indexOf('BAW123'), html.indexOf('LHR'), html.indexOf('<hr'),
            html.indexOf('Type:'), html.indexOf('Signal lost'),
        ];
        expect(order).toEqual([...order].sort((a, b) => a - b));
    });
});
