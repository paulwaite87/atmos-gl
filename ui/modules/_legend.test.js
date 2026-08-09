// Tests for the shared legend-slot plumbing behind sst.js/waves.js/currents.js
// (architecture review candidate "a home for copy-pasted legend/hover-popup
// plumbing"). vitest runs in the default "node" environment (no jsdom/happy-dom
// dependency in this repo), so `document` is faked minimally here rather than
// relying on a real DOM -- same approach _reconcile.test.js takes for `window`.
// Legend keys render entirely client-side now (issue #302) via _keycanvas.js's
// drawKey() onto a <canvas> -- so the fake document also stubs a 2D canvas context.
import { describe, test, expect, beforeEach } from 'vitest';
import {
    removeLegend, replaceSlot, initLegendToggle, insertBeforeExtension, standardLegend,
} from './_legend.js';

function fakeCanvasContext() {
    return {
        setTransform: () => {}, clearRect: () => {}, fillRect: () => {},
        fillText: () => {}, beginPath: () => {}, moveTo: () => {}, lineTo: () => {},
        stroke: () => {},
    };
}

function fakeDocument() {
    const byId = {};
    const makeElement = (tag) => {
        const el = {
            id: '', className: '', style: {}, children: [], textContent: '', title: '',
            _classes: new Set(), _tag: tag,
        };
        el.appendChild = (child) => {
            el.children.push(child);
            if (child.id) byId[child.id] = child;
        };
        el.insertBefore = (child, ref) => {
            const idx = ref ? el.children.indexOf(ref) : -1;
            if (idx === -1) el.children.push(child); else el.children.splice(idx, 0, child);
            if (child.id) byId[child.id] = child;
        };
        Object.defineProperty(el, 'firstChild', { get: () => el.children[0] ?? null });
        el.classList = {
            toggle: (name, force) => {
                const shouldHave = force !== undefined ? force : !el._classes.has(name);
                if (shouldHave) el._classes.add(name); else el._classes.delete(name);
                return shouldHave;
            },
            contains: (name) => el._classes.has(name),
        };
        el.addEventListener = (evt, fn) => { (el._listeners ??= {})[evt] = fn; };
        el.remove = () => { delete byId[el.id]; };
        if (tag === 'canvas') el.getContext = () => fakeCanvasContext();
        return el;
    };
    const stack = makeElement('div');
    stack.id = 'legend-stack';
    byId['legend-stack'] = stack;
    return {
        getElementById: (id) => byId[id] || null,
        createElement: makeElement,
        _stack: stack,
    };
}

function fakeLocalStorage() {
    const store = {};
    return {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: (k) => { delete store[k]; },
        _store: store,
    };
}

describe('insertBeforeExtension', () => {
    test('inserts the given suffix before the extension', () => {
        expect(insertBeforeExtension('sst.png', '_anomaly')).toBe('sst_anomaly.png');
    });

    test('handles a multi-dot filename by splitting on the last dot', () => {
        expect(insertBeforeExtension('waves.data.png', '_key')).toBe('waves.data_key.png');
    });

    test('appends the suffix with no extension when there is no dot', () => {
        expect(insertBeforeExtension('sst', '_key')).toBe('sst_key');
    });
});

describe('standardLegend', () => {
    beforeEach(() => {
        globalThis.document = fakeDocument();
        globalThis.window = { MAP_UI: 'http://test' };
    });

    const renderKeyFor = (cfg) => ({
        lut: new Uint8Array(256 * 4).fill(255), vmin: 0, vmax: 1, ticks: [0, 1], title: cfg.outfile,
    });

    test('addLegend draws a <canvas> key into the slot', () => {
        const legend = standardLegend('sst-legend-slot', renderKeyFor, 0.85);

        legend.addLegend({ outfile: 'sst.png', opacity: 50 });

        const slot = document.getElementById('sst-legend-slot');
        expect(slot).not.toBeNull();
        expect(slot.children[0]._tag).toBe('canvas');
    });

    test('addLegend hides the slot when cfg.opacity is explicitly 0', () => {
        const legend = standardLegend('sst-legend-slot', renderKeyFor, 0.85);

        legend.addLegend({ outfile: 'sst.png', opacity: 0 });

        expect(document.getElementById('sst-legend-slot')).toBeNull();
    });

    test('addLegend always shows (ignores cfg.opacity) when opacityFallback is omitted', () => {
        // Matches currents.js/jetstream.js: particles stay visible independent of the
        // fill's own opacity, so the legend key must never hide on opacity=0 either.
        const legend = standardLegend('currents-legend-slot', renderKeyFor);

        legend.addLegend({ outfile: 'currents.png', opacity: 0 });

        expect(document.getElementById('currents-legend-slot')).not.toBeNull();
    });

    test('replaces an existing slot with the same id rather than duplicating it', () => {
        const legend = standardLegend('sst-legend-slot', renderKeyFor, 1);
        legend.addLegend({ outfile: 'sst.png' });
        legend.addLegend({ outfile: 'sst.png' });

        // The old slot removed itself from the id registry; the live one (still
        // physically present in the fake stack's children array, mirroring
        // replaceSlot's own equivalent test below) is the second.
        const stack = document._stack;
        expect(stack.children.filter((c) => c.id === 'sst-legend-slot').length).toBe(2);
        expect(document.getElementById('sst-legend-slot')).toBe(stack.children[1]);
    });

    test('removeLegend removes the slot', () => {
        const legend = standardLegend('sst-legend-slot', renderKeyFor, 1);
        legend.addLegend({ outfile: 'sst.png' });

        legend.removeLegend();

        expect(document.getElementById('sst-legend-slot')).toBeNull();
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

    test('also supports appendChild-style content (the canvas-key shape)', () => {
        replaceSlot('sst-legend-slot', (slot) => {
            const canvas = document.createElement('canvas');
            slot.appendChild(canvas);
        });
        expect(document.getElementById('sst-legend-slot').children[0]._tag).toBe('canvas');
    });
});

describe('removeLegend', () => {
    beforeEach(() => {
        globalThis.document = fakeDocument();
    });

    test('removes the slot by id', () => {
        replaceSlot('sst-legend-slot', () => {});
        removeLegend('sst-legend-slot');
        expect(document.getElementById('sst-legend-slot')).toBeNull();
    });

    test('is a no-op when the slot does not exist', () => {
        expect(() => removeLegend('nonexistent-slot')).not.toThrow();
    });
});

describe('initLegendToggle', () => {
    beforeEach(() => {
        globalThis.document = fakeDocument();
        globalThis.localStorage = fakeLocalStorage();
    });

    test('does nothing when #legend-stack is absent', () => {
        globalThis.document.getElementById = () => null;
        expect(() => initLegendToggle()).not.toThrow();
    });

    test('inserts a single #legend-toggle button as the first child of the stack', () => {
        initLegendToggle();
        const stack = document._stack;
        expect(stack.children.length).toBe(1);
        expect(stack.firstChild.id).toBe('legend-toggle');
    });

    test('defaults to shown ("−") when localStorage has no prior preference', () => {
        initLegendToggle();
        const btn = document.getElementById('legend-toggle');
        expect(document._stack.classList.contains('legends-hidden')).toBe(false);
        expect(btn.textContent).toBe('−');
        expect(btn.title).toBe('Hide legends');
    });

    test('restores a previously hidden state from localStorage on init', () => {
        localStorage.setItem('atmosgl.legendsHidden', 'true');
        initLegendToggle();
        const btn = document.getElementById('legend-toggle');
        expect(document._stack.classList.contains('legends-hidden')).toBe(true);
        expect(btn.textContent).toBe('+');
        expect(btn.title).toBe('Show legends');
    });

    test('clicking toggles the class, the symbol, and persists to localStorage', () => {
        initLegendToggle();
        const stack = document._stack;
        const btn = document.getElementById('legend-toggle');

        btn._listeners.click();
        expect(stack.classList.contains('legends-hidden')).toBe(true);
        expect(btn.textContent).toBe('+');
        expect(localStorage.getItem('atmosgl.legendsHidden')).toBe('true');

        btn._listeners.click();
        expect(stack.classList.contains('legends-hidden')).toBe(false);
        expect(btn.textContent).toBe('−');
        expect(localStorage.getItem('atmosgl.legendsHidden')).toBe('false');
    });

    test('is idempotent -- calling it again does not create a second toggle', () => {
        initLegendToggle();
        initLegendToggle();
        const stack = document._stack;
        expect(stack.children.filter((c) => c.id === 'legend-toggle').length).toBe(1);
    });

    test('new legend slots are appended after the toggle, which stays first', () => {
        initLegendToggle();
        replaceSlot('sst-legend-slot', () => {});

        const stack = document._stack;
        expect(stack.firstChild.id).toBe('legend-toggle');
        expect(stack.children[1].id).toBe('sst-legend-slot');
    });
});
