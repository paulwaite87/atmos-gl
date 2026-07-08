// Tests for the shared legend-slot plumbing behind sst.js/waves.js/currents.js
// (architecture review candidate "a home for copy-pasted legend/hover-popup
// plumbing"). vitest runs in the default "node" environment (no jsdom/happy-dom
// dependency in this repo), so `document` is faked minimally here rather than
// relying on a real DOM -- same approach _reconcile.test.js takes for `window`.
import { describe, test, expect, beforeEach } from 'vitest';
import { keyFilename, showLegend, removeLegend, replaceSlot } from './_legend.js';

function fakeDocument() {
    const byId = {};
    const makeElement = () => {
        const el = { id: '', className: '', style: {}, children: [] };
        el.appendChild = (child) => {
            el.children.push(child);
            if (child.id) byId[child.id] = child;
        };
        el.remove = () => { delete byId[el.id]; };
        return el;
    };
    const stack = makeElement();
    stack.id = 'legend-stack';
    byId['legend-stack'] = stack;
    return {
        getElementById: (id) => byId[id] || null,
        createElement: makeElement,
        _stack: stack,
    };
}

describe('keyFilename', () => {
    test('inserts _key before the extension', () => {
        expect(keyFilename('sst.png')).toBe('sst_key.png');
    });

    test('handles a multi-dot outfile by splitting on the last dot', () => {
        expect(keyFilename('waves.data.png')).toBe('waves.data_key.png');
    });

    test('appends _key with no extension when there is no dot', () => {
        expect(keyFilename('sst')).toBe('sst_key');
    });
});

describe('showLegend / removeLegend', () => {
    beforeEach(() => {
        globalThis.document = fakeDocument();
    });

    test('does nothing when #legend-stack is absent', () => {
        globalThis.document = fakeDocument();
        globalThis.document.getElementById = (id) => (id === 'legend-stack' ? null : null);
        expect(() => showLegend('sst-legend-slot', 'http://test/sst_key.png')).not.toThrow();
    });

    test('appends a slot with an <img> pointing at the given url', () => {
        showLegend('sst-legend-slot', 'http://test/sst_key.png');
        const slot = document.getElementById('sst-legend-slot');
        expect(slot).not.toBeNull();
        expect(slot.className).toBe('legend-slot');
        expect(slot.children[0].src).toBe('http://test/sst_key.png');
    });

    test('replaces an existing slot with the same id rather than duplicating it', () => {
        showLegend('sst-legend-slot', 'http://test/sst_key.png?t=1');
        showLegend('sst-legend-slot', 'http://test/sst_key.png?t=2');

        const stack = document._stack;
        expect(stack.children.filter((c) => c.id === 'sst-legend-slot').length).toBe(2);
        // the first slot removed itself from the registry; the live one is the second
        expect(document.getElementById('sst-legend-slot').children[0].src)
            .toBe('http://test/sst_key.png?t=2');
    });

    test('removeLegend removes the slot by id', () => {
        showLegend('sst-legend-slot', 'http://test/sst_key.png');
        removeLegend('sst-legend-slot');
        expect(document.getElementById('sst-legend-slot')).toBeNull();
    });

    test('removeLegend is a no-op when the slot does not exist', () => {
        expect(() => removeLegend('nonexistent-slot')).not.toThrow();
    });
});

describe('replaceSlot', () => {
    beforeEach(() => {
        globalThis.document = fakeDocument();
    });

    test('does nothing when #legend-stack is absent', () => {
        globalThis.document = fakeDocument();
        globalThis.document.getElementById = (id) => (id === 'legend-stack' ? null : null);
        expect(() => replaceSlot('wind-legend-slot', () => {})).not.toThrow();
    });

    test('creates a legend-slot div and calls populate with it', () => {
        let seen = null;
        replaceSlot('wind-legend-slot', (slot) => { seen = slot; slot.innerHTML = '<b>hi</b>'; });
        const slot = document.getElementById('wind-legend-slot');
        expect(slot).not.toBeNull();
        expect(slot).toBe(seen);
        expect(slot.className).toBe('legend-slot');
        expect(slot.innerHTML).toBe('<b>hi</b>');
    });

    test('replaces an existing slot with the same id rather than duplicating it', () => {
        replaceSlot('wind-legend-slot', (slot) => { slot.innerHTML = 'first'; });
        replaceSlot('wind-legend-slot', (slot) => { slot.innerHTML = 'second'; });

        const stack = document._stack;
        expect(stack.children.filter((c) => c.id === 'wind-legend-slot').length).toBe(2);
        expect(document.getElementById('wind-legend-slot').innerHTML).toBe('second');
    });

    test('also supports appendChild-style content (the showLegend shape)', () => {
        replaceSlot('sst-legend-slot', (slot) => {
            const img = document.createElement('img');
            img.src = 'http://test/x.png';
            slot.appendChild(img);
        });
        expect(document.getElementById('sst-legend-slot').children[0].src).toBe('http://test/x.png');
    });
});
