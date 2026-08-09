import { describe, test, expect } from 'vitest';
import { buildScaledLUT, twoSlopePos } from './_colormaps.js';

// A tiny 4-stop RGB source cmap (Uint8Array(4*3)) for exact, easy-to-check assertions:
// black -> red -> green -> blue, evenly spaced across its own [0,1].
const TINY_CMAP = new Uint8Array([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255]);

describe('buildScaledLUT', () => {
    test('returns a 256-entry RGBA Uint8Array with alpha always 255', () => {
        const lut = buildScaledLUT({
            physicalMin: 0, physicalMax: 10,
            toPos: (v) => v / 10,
            sourceCmap: TINY_CMAP,
        });
        expect(lut.length).toBe(256 * 4);
        for (let i = 0; i < 256; i++) expect(lut[i * 4 + 3]).toBe(255);
    });

    test('a plain linear toPos samples the source cmap at the matching fraction', () => {
        // physicalMin=0, physicalMax=10, displayMin=0, displayMax=10 -> toPos is identity,
        // so LUT texel i should sample TINY_CMAP at the same fraction i/255.
        const lut = buildScaledLUT({
            physicalMin: 0, physicalMax: 10,
            toPos: (v) => v / 10,
            sourceCmap: TINY_CMAP,
        });
        // i=0 -> t=0 -> first stop (black)
        expect([lut[0], lut[1], lut[2]]).toEqual([0, 0, 0]);
        // i=255 -> t=1 -> last stop (blue)
        expect([lut[255 * 4], lut[255 * 4 + 1], lut[255 * 4 + 2]]).toEqual([0, 0, 255]);
    });

    test('a display window narrower than the physical domain clamps outside it', () => {
        // physical domain 0..10, but the LIVE display window is 4..6 -- values below 4
        // must clamp to the first stop, values above 6 to the last stop.
        const toPos = (v) => (v - 4) / (6 - 4);
        const lut = buildScaledLUT({ physicalMin: 0, physicalMax: 10, toPos, sourceCmap: TINY_CMAP });

        // physical value 0 (well below displayMin=4) -> t clamps to 0 -> first stop.
        expect([lut[0], lut[1], lut[2]]).toEqual([0, 0, 0]);
        // physical value 10 (well above displayMax=6) -> t clamps to 1 -> last stop.
        expect([lut[255 * 4], lut[255 * 4 + 1], lut[255 * 4 + 2]]).toEqual([0, 0, 255]);
    });

    test('composes with twoSlopePos for a zero-centred anomaly remap', () => {
        // Physical domain -10..10; live display range is an asymmetric -2..4 anomaly.
        // Physical value 0 must land at LUT texel 128-ish (t=0.5, twoSlopePos's own centre).
        const lut = buildScaledLUT({
            physicalMin: -10, physicalMax: 10,
            toPos: twoSlopePos(-2, 4),
            sourceCmap: TINY_CMAP,
        });
        const midIdx = Math.round(0.5 * 255) * 4;
        // TINY_CMAP's t=0.5 stop is its 3rd of 4 (index 2 -> green).
        expect([lut[midIdx], lut[midIdx + 1], lut[midIdx + 2]]).toEqual([0, 255, 0]);
    });
});
