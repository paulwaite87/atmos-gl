// Tests for keepLayersOnTop -- see its own docstring for why this must read
// map.getLayersOrder() (the true internal paint order, custom layers included)
// rather than map.getStyle().layers (which MapLibre omits every `type: 'custom'`
// layer from -- every GPU fill layer in this app).
import { describe, test, expect } from 'vitest';
import { keepLayersOnTop } from './_layerstack.js';

function fakeMap(order) {
    const moved = [];
    return {
        _order: order,
        getLayersOrder: () => order,
        getLayer: (id) => (order.includes(id) ? { id } : undefined),
        moveLayer: (id) => {
            moved.push(id);
            order.splice(order.indexOf(id), 1);
            order.push(id);
        },
        moved,
    };
}

describe('keepLayersOnTop', () => {
    test('does nothing when the top layer is already an acceptable id', () => {
        const map = fakeMap(['base', 'landmass-halo', 'landmass-line']);
        keepLayersOnTop(map, ['landmass-halo', 'landmass-line'], ['landmass-line', 'markers-labels']);
        expect(map.moved).toEqual([]);
    });

    test('reclaims the top when a custom fill layer sits above it, via getLayersOrder', () => {
        // This is the exact shape of the bug: a `type: 'custom'` fill layer (e.g.
        // temperature-fill-layer) is now above landmass-line in the TRUE paint
        // order -- getLayersOrder() surfaces it, unlike getStyle().layers.
        const map = fakeMap(['base', 'landmass-halo', 'landmass-line', 'temperature-fill-layer']);
        keepLayersOnTop(map, ['landmass-halo', 'landmass-line'], ['landmass-line', 'markers-labels']);
        expect(map.moved).toEqual(['landmass-halo', 'landmass-line']);
        expect(map._order[map._order.length - 1]).toBe('landmass-line');
    });

    test('accepts any of several acceptable top ids (markers-labels case)', () => {
        const map = fakeMap(['base', 'landmass-halo', 'landmass-line', 'markers-dots', 'markers-labels']);
        keepLayersOnTop(map, ['landmass-halo', 'landmass-line'], ['landmass-line', 'markers-labels']);
        expect(map.moved).toEqual([]);
    });

    test('skips moving an id the layer no longer has', () => {
        const map = fakeMap(['base', 'landmass-line', 'other-fill']);
        keepLayersOnTop(map, ['landmass-halo', 'landmass-line'], ['landmass-line']);
        // landmass-halo isn't present (e.g. removed already) -- must not throw, and
        // only the id that actually exists gets moved.
        expect(map.moved).toEqual(['landmass-line']);
    });

    test('is a no-op against an empty order', () => {
        const map = fakeMap([]);
        expect(() => keepLayersOnTop(map, ['landmass-line'], ['landmass-line'])).not.toThrow();
        expect(map.moved).toEqual([]);
    });
});
