// Tests for the shared legend-slot plumbing behind sst.js/waves.js/currents.js
// (architecture review candidate "a home for copy-pasted legend/hover-popup
// plumbing"). vitest runs in the default "node" environment (no jsdom/happy-dom
// dependency in this repo), so `document` is faked minimally here rather than
// relying on a real DOM -- same approach _reconcile.test.js takes for `window`.
import { describe, test, expect, beforeEach } from 'vitest';
import { keyFilename, showLegend, removeLegend, replaceSlot, initLegendToggle } from './_legend.js';

function fakeDocument() {
    const byId = {};
    const makeElement = () => {
        const el = {
            id: '', className: '', style: {}, children: [], textContent: '', title: '',
            _classes: new Set(),
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

function fakeLocalStorage() {
    const store = {};
    return {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: (k) => { delete store[k]; },
        _store: store,
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
        showLegend('sst-legend-slot', 'http://test/sst_key.png');

        const stack = document._stack;
        expect(stack.firstChild.id).toBe('legend-toggle');
        expect(stack.children[1].id).toBe('sst-legend-slot');
    });
});
