// Tests for the shared hover-popup wiring behind quakes.js/storms.js/volcanoes.js/
// satellites.js (architecture review candidate "a home for copy-pasted legend/
// hover-popup plumbing"). vitest runs in the default "node" environment, so
// maplibregl.Popup and the map object are faked minimally here.
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { hoverPopup } from './_hoverpopup.js';

function fakePopup() {
    const p = { html: null, lngLat: null, onMap: false };
    p.setLngLat = vi.fn((c) => { p.lngLat = c; return p; });
    p.setHTML = vi.fn((h) => { p.html = h; return p; });
    p.addTo = vi.fn(() => { p.onMap = true; return p; });
    p.remove = vi.fn(() => { p.onMap = false; return p; });
    return p;
}

function fakeMap() {
    const handlers = {};
    const canvas = { style: { cursor: '' } };
    return {
        _handlers: handlers,
        getCanvas: () => canvas,
        on: vi.fn((evt, layerId, fn) => { handlers[`${evt}:${layerId}`] = fn; }),
        off: vi.fn((evt, layerId, fn) => {
            if (handlers[`${evt}:${layerId}`] === fn) delete handlers[`${evt}:${layerId}`];
        }),
    };
}

beforeEach(() => {
    globalThis.maplibregl = { Popup: vi.fn(fakePopup) };
});

describe('hoverPopup', () => {
    test('registers mouseenter/mouseleave on the given layer', () => {
        const map = fakeMap();
        hoverPopup(map, 'quakes-layer', { html: () => '<div/>' });

        expect(map.on).toHaveBeenCalledWith('mouseenter', 'quakes-layer', expect.any(Function));
        expect(map.on).toHaveBeenCalledWith('mouseleave', 'quakes-layer', expect.any(Function));
    });

    test('mouseenter sets the cursor, positions the popup via html(feature), and adds it to the map', () => {
        const map = fakeMap();
        const html = vi.fn((f) => `<strong>${f.properties.name}</strong>`);
        hoverPopup(map, 'quakes-layer', { html });

        const feature = { properties: { name: 'M 4.2' }, geometry: { coordinates: [1, 2] } };
        map._handlers['mouseenter:quakes-layer']({ features: [feature] });

        expect(map.getCanvas().style.cursor).toBe('pointer');
        expect(html).toHaveBeenCalledWith(feature);
        const popup = globalThis.maplibregl.Popup.mock.results[0].value;
        expect(popup.setLngLat).toHaveBeenCalledWith([1, 2]);
        expect(popup.setHTML).toHaveBeenCalledWith('<strong>M 4.2</strong>');
        expect(popup.addTo).toHaveBeenCalledWith(map);
    });

    test('mouseenter with no features is a no-op', () => {
        const map = fakeMap();
        const html = vi.fn();
        hoverPopup(map, 'quakes-layer', { html });

        map._handlers['mouseenter:quakes-layer']({ features: [] });

        expect(html).not.toHaveBeenCalled();
        const popup = globalThis.maplibregl.Popup.mock.results[0].value;
        expect(popup.addTo).not.toHaveBeenCalled();
    });

    test('mouseleave resets the cursor and removes the popup', () => {
        const map = fakeMap();
        hoverPopup(map, 'quakes-layer', { html: () => '<div/>' });

        map._handlers['mouseenter:quakes-layer']({
            features: [{ properties: {}, geometry: { coordinates: [0, 0] } }],
        });
        map._handlers['mouseleave:quakes-layer']();

        expect(map.getCanvas().style.cursor).toBe('');
        const popup = globalThis.maplibregl.Popup.mock.results[0].value;
        expect(popup.remove).toHaveBeenCalled();
    });

    test('passes offset through to the Popup constructor, defaulting to 15', () => {
        const map = fakeMap();
        hoverPopup(map, 'quakes-layer', { html: () => '<div/>' });
        expect(globalThis.maplibregl.Popup).toHaveBeenCalledWith(
            expect.objectContaining({ offset: 15 }));

        hoverPopup(map, 'storms-points', { offset: 10, html: () => '<div/>' });
        expect(globalThis.maplibregl.Popup).toHaveBeenLastCalledWith(
            expect.objectContaining({ offset: 10 }));
    });

    test('maxWidth is omitted from the Popup constructor call when not given', () => {
        const map = fakeMap();
        hoverPopup(map, 'quakes-layer', { html: () => '<div/>' });

        const opts = globalThis.maplibregl.Popup.mock.calls[0][0];
        expect('maxWidth' in opts).toBe(false);
    });

    test('an explicit maxWidth is passed through to the Popup constructor', () => {
        const map = fakeMap();
        hoverPopup(map, 'storms-points', { html: () => '<div/>', maxWidth: '360px' });

        expect(globalThis.maplibregl.Popup).toHaveBeenCalledWith(
            expect.objectContaining({ maxWidth: '360px' }));
    });

    test('the returned stop() unregisters both handlers and removes the popup', () => {
        const map = fakeMap();
        const stop = hoverPopup(map, 'quakes-layer', { html: () => '<div/>' });
        const popup = globalThis.maplibregl.Popup.mock.results[0].value;

        stop();

        expect(map._handlers['mouseenter:quakes-layer']).toBeUndefined();
        expect(map._handlers['mouseleave:quakes-layer']).toBeUndefined();
        expect(popup.remove).toHaveBeenCalled();
    });
});
