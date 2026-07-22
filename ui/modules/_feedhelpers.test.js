// Tests for the shared fetch/icon/popup-card plumbing behind the six event-feed
// layers (architecture review candidate "six frontend event-feed modules copy-paste
// the same load scaffold"). fetch/window/createImageBitmap are faked minimally, same
// approach _reconcile.test.js/_legend.test.js take for browser globals.
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fetchOrThrow, preloadIcons, popupCard } from './_feedhelpers.js';

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

describe('popupCard', () => {
    test('renders the title, hr, and each row with default width', () => {
        const html = popupCard({
            title: 'Test Volcano',
            rows: [{ label: 'VEI', value: 4 }],
        });
        expect(html).toContain('Test Volcano');
        expect(html).toContain('<hr');
        expect(html).toContain('VEI:');
        expect(html).toContain('<strong>4</strong>');
        expect(html).toContain('width:45px');
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
