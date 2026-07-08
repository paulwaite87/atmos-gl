// Tests for the shared pulse-animation RAF loop behind storms.js/satellites.js
// (architecture review candidate "a home for copy-pasted legend/hover-popup
// plumbing"). requestAnimationFrame is faked as a synchronous microtask-driven queue
// so the loop can be driven deterministically without real frame timing.
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { startPulse } from './_pulse.js';

function fakeMap(layerExists = true) {
    return {
        _exists: layerExists,
        getLayer: vi.fn(function () { return this._exists ? {} : null; }),
        setPaintProperty: vi.fn(),
    };
}

// Drains one requestAnimationFrame callback (there's at most one pending at a time,
// since the loop only schedules its next frame from inside the current one).
function tick() {
    const cb = globalThis.requestAnimationFrame.mock.calls.at(-1)?.[0];
    if (cb) cb();
}

beforeEach(() => {
    globalThis.requestAnimationFrame = vi.fn();
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('startPulse', () => {
    test('schedules a frame immediately', () => {
        startPulse(fakeMap(), 'sat-position', 'circle-radius', { base: 5 });
        expect(globalThis.requestAnimationFrame).toHaveBeenCalledTimes(1);
    });

    test('sets the paint property to a number in [base, base+amp] by default', () => {
        const map = fakeMap();
        startPulse(map, 'sat-position', 'circle-radius', { base: 5, amp: 4 });
        tick();

        expect(map.setPaintProperty).toHaveBeenCalledTimes(1);
        const [layerId, prop, value] = map.setPaintProperty.mock.calls[0];
        expect(layerId).toBe('sat-position');
        expect(prop).toBe('circle-radius');
        expect(value).toBeGreaterThanOrEqual(5);
        expect(value).toBeLessThanOrEqual(9);
    });

    test('routes the oscillating radius through toValue when supplied', () => {
        const map = fakeMap();
        const toValue = vi.fn((r) => ['match', ['get', 'record_type'], 'CURRENT', r, 4]);
        startPulse(map, 'storms-points', 'circle-radius', { base: 6, toValue });
        tick();

        expect(toValue).toHaveBeenCalledTimes(1);
        const [, , value] = map.setPaintProperty.mock.calls[0];
        expect(value[0]).toBe('match');
    });

    test('keeps scheduling frames while the layer exists', () => {
        const map = fakeMap();
        startPulse(map, 'sat-position', 'circle-radius', { base: 5 });
        tick(); tick(); tick();

        expect(globalThis.requestAnimationFrame).toHaveBeenCalledTimes(4); // initial + 3
        expect(map.setPaintProperty).toHaveBeenCalledTimes(3);
    });

    test('stops scheduling once the layer is gone', () => {
        const map = fakeMap(true);
        startPulse(map, 'sat-position', 'circle-radius', { base: 5 });
        tick();
        map._exists = false;
        tick();

        expect(globalThis.requestAnimationFrame).toHaveBeenCalledTimes(2); // initial + 1
        expect(map.setPaintProperty).toHaveBeenCalledTimes(1);
    });

    test('the returned stop() halts further frames', () => {
        const map = fakeMap();
        const stop = startPulse(map, 'sat-position', 'circle-radius', { base: 5 });
        tick();
        stop();
        tick();

        expect(globalThis.requestAnimationFrame).toHaveBeenCalledTimes(2); // initial + 1
        expect(map.setPaintProperty).toHaveBeenCalledTimes(1);
    });
});
