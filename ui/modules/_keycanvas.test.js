// Tests for _keycanvas.js's drawKey() -- the client-side replacement for the
// (removed) backend PlottingMixin.save_key_image() (issue #302). Draws onto a
// <canvas>; since vitest runs in the default "node" environment (no jsdom/happy-dom
// dependency in this repo -- see _legend.test.js's identical note), the canvas and
// its 2D context are faked here, recording every draw call for assertions.
import { describe, test, expect, beforeEach } from 'vitest';
import { drawKey } from './_keycanvas.js';

function fakeCanvas() {
    const calls = [];
    const ctx = {
        fillStyle: null, font: null, textAlign: null, textBaseline: null,
        strokeStyle: null, lineWidth: null,
        setTransform: (...a) => calls.push(['setTransform', ...a]),
        clearRect: (...a) => calls.push(['clearRect', ...a]),
        fillRect: (...a) => calls.push(['fillRect', ...a, ctx.fillStyle]),
        fillText: (...a) => calls.push(['fillText', ...a]),
        beginPath: () => calls.push(['beginPath']),
        moveTo: (...a) => calls.push(['moveTo', ...a]),
        lineTo: (...a) => calls.push(['lineTo', ...a]),
        stroke: () => calls.push(['stroke']),
    };
    const canvas = { width: 0, height: 0, style: {}, getContext: () => ctx };
    return { canvas, ctx, calls };
}

// A LUT with a distinct, easily-asserted colour at each end.
function endpointLut() {
    const lut = new Uint8Array(256 * 4);
    lut.set([10, 20, 30, 255], 0);          // t=0
    lut.set([200, 210, 220, 128], 255 * 4); // t=1 (alpha 128 -> 128/255 in rgba())
    return lut;
}

beforeEach(() => {
    globalThis.window = { devicePixelRatio: 1 };
});

describe('drawKey', () => {
    test('sizes the canvas to the fixed CSS dimensions scaled by devicePixelRatio', () => {
        globalThis.window = { devicePixelRatio: 2 };
        const { canvas } = fakeCanvas();

        drawKey(canvas, { lut: endpointLut(), vmin: 0, vmax: 1, ticks: [], title: 't' });

        expect(canvas.width).toBe(400);   // 200 * 2
        expect(canvas.height).toBe(108);  // 54 * 2
        expect(canvas.style.width).toBe('200px');
        expect(canvas.style.height).toBe('54px');
    });

    test('draws the title text centred above the bar', () => {
        const { canvas, calls } = fakeCanvas();

        drawKey(canvas, { lut: endpointLut(), vmin: 0, vmax: 1, ticks: [], title: 'Wind speed (km/h)' });

        const titleCall = calls.find((c) => c[0] === 'fillText' && c[1] === 'Wind speed (km/h)');
        expect(titleCall).toBeDefined();
        expect(titleCall[2]).toBe(100);  // CSS_WIDTH / 2
        expect(titleCall[3]).toBe(13);
    });

    test('paints the bar by sampling the LUT directly at each pixel fraction', () => {
        const { canvas, calls } = fakeCanvas();

        drawKey(canvas, { lut: endpointLut(), vmin: 0, vmax: 100, ticks: [], title: '' });

        const barFills = calls.filter((c) => c[0] === 'fillRect' && c[4] === 14);
        // barW = 200 - 2*4 = 192 one-pixel-wide fillRect calls.
        expect(barFills.length).toBe(192);
        expect(barFills[0][5]).toBe('rgba(10,20,30,1)');            // t=0 -> LUT start
        expect(barFills[barFills.length - 1][5]).toBe(`rgba(200,210,220,${128 / 255})`); // t=1 -> LUT end
    });

    test('places ticks at the default linear (v - vmin) / (vmax - vmin) position', () => {
        const { canvas, calls } = fakeCanvas();

        drawKey(canvas, { lut: endpointLut(), vmin: 0, vmax: 10, ticks: [0, 5, 10], title: '' });

        const moves = calls.filter((c) => c[0] === 'moveTo');
        expect(moves.map((m) => m[1])).toEqual([4, 100, 196]); // barX=4, barW=192
    });

    test('clamps tick positions outside [0,1] to the bar edges', () => {
        const { canvas, calls } = fakeCanvas();

        drawKey(canvas, { lut: endpointLut(), vmin: 0, vmax: 10, ticks: [-5, 20], title: '' });

        const moves = calls.filter((c) => c[0] === 'moveTo');
        expect(moves.map((m) => m[1])).toEqual([4, 196]);
    });

    test('a custom toPos overrides tick placement without affecting the bar colour', () => {
        const { canvas, calls } = fakeCanvas();
        // TwoSlopeNorm-style: vcenter=0 always lands at the midpoint, regardless of
        // vmin/vmax being asymmetric.
        const toPos = (v) => (v <= 0 ? 0.5 * (v - -4) / 4 : 0.5 + 0.5 * v / 8);

        drawKey(canvas, {
            lut: endpointLut(), vmin: -4, vmax: 8, ticks: [-4, 0, 8], title: '', toPos,
        });

        const moves = calls.filter((c) => c[0] === 'moveTo');
        expect(moves.map((m) => m[1])).toEqual([4, 100, 196]); // -4->0, 0->0.5, 8->1.0

        const barFills = calls.filter((c) => c[0] === 'fillRect' && c[4] === 14);
        expect(barFills[0][5]).toBe('rgba(10,20,30,1)');  // bar still samples t=px/(barW-1)
    });

    test.each([
        ['%d', 3.7, '4'],
        ['%.1f', 3.14, '3.1'],
        ['%.2f', 3.14159, '3.14'],
        [undefined, 3.14159, '3.14159'],
    ])('formats a tick label with tickFormat=%s', (tickFormat, value, expected) => {
        const { canvas, calls } = fakeCanvas();

        drawKey(canvas, { lut: endpointLut(), vmin: 0, vmax: 10, ticks: [value], title: '', tickFormat });

        const label = calls.find((c) => c[0] === 'fillText' && c[1] === expected);
        expect(label).toBeDefined();
    });

    test('a function tickFormat is called directly with the tick value', () => {
        const { canvas, calls } = fakeCanvas();
        const tickFormat = (v) => `${v}kt`;

        drawKey(canvas, { lut: endpointLut(), vmin: 0, vmax: 10, ticks: [5], title: '', tickFormat });

        expect(calls.some((c) => c[0] === 'fillText' && c[1] === '5kt')).toBe(true);
    });

    test('the decorate hook runs after the bar is painted but before ticks are drawn', () => {
        const { canvas, calls } = fakeCanvas();
        const order = [];
        const decorate = (ctx, { barX, barY, barW, barH, toX }) => {
            order.push('decorate');
            expect(barX).toBe(4);
            expect(barY).toBe(22);
            expect(barW).toBe(192);
            expect(barH).toBe(14);
            expect(toX(5)).toBeCloseTo(100);
        };

        drawKey(canvas, {
            lut: endpointLut(), vmin: 0, vmax: 10, ticks: [5], title: '', decorate,
        });

        const barFillIdx = calls.findIndex((c) => c[0] === 'fillRect' && c[4] === 14);
        const lastBarFillIdx = calls.map((c) => c[0]).lastIndexOf('fillRect');
        const tickMoveIdx = calls.findIndex((c) => c[0] === 'moveTo');
        expect(order).toEqual(['decorate']);
        expect(barFillIdx).toBeLessThan(tickMoveIdx);
        expect(lastBarFillIdx).toBeLessThan(tickMoveIdx);
    });

    test('stride=3 (RGB, no alpha channel) samples fully opaque', () => {
        const { canvas, calls } = fakeCanvas();
        const lut = new Uint8Array(256 * 3);
        lut.set([50, 60, 70], 0);

        drawKey(canvas, { lut, stride: 3, vmin: 0, vmax: 1, ticks: [], title: '' });

        const barFills = calls.filter((c) => c[0] === 'fillRect' && c[4] === 14);
        expect(barFills[0][5]).toBe('rgba(50,60,70,1)');
    });
});
