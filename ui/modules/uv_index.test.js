import { describe, expect, test } from 'vitest';
import { MODELLED_NOTE, buildBandLUT, legendItems } from './uv_index.js';

// The bands exactly as GET /api/config serves them (tasks/scalar_field.py SPECS["uv_index"]).
const SPEC = {
    vmin: 0, vmax: 14, visible_from: 1,
    bands: [
        { from: 0, name: 'Low', color: '#4eb400' },
        { from: 3, name: 'Moderate', color: '#f7e400' },
        { from: 6, name: 'High', color: '#f85900' },
        { from: 8, name: 'Very high', color: '#d8001d' },
        { from: 11, name: 'Extreme', color: '#6b49c8' },
    ],
};

// RGBA of the LUT texel the shader samples for a UVI value.
const texel = (lut, uvi) => {
    const i = Math.round((uvi / (SPEC.vmax - SPEC.vmin)) * 255);
    return Array.from(lut.slice(i * 4, i * 4 + 4));
};

describe('legendItems', () => {
    test('lists the five WHO bands with their ranges and colours', () => {
        expect(legendItems(SPEC.bands)).toEqual([
            { color: '#4eb400', label: 'Low 0–2' },
            { color: '#f7e400', label: 'Moderate 3–5' },
            { color: '#f85900', label: 'High 6–7' },
            { color: '#d8001d', label: 'Very high 8–10' },
            { color: '#6b49c8', label: 'Extreme 11+' },
        ]);
    });
});

describe('buildBandLUT', () => {
    const lut = buildBandLUT(SPEC);

    test('is a 256-entry RGBA table', () => {
        expect(lut).toBeInstanceOf(Uint8Array);
        expect(lut.length).toBe(256 * 4);
    });

    test('is fully transparent below UVI 1', () => {
        expect(texel(lut, 0)[3]).toBe(0);
        expect(texel(lut, 0.5)[3]).toBe(0);
    });

    test('fills each band with its flat WHO colour, opaque', () => {
        expect(texel(lut, 2)).toEqual([0x4e, 0xb4, 0x00, 255]);
        expect(texel(lut, 4.5)).toEqual([0xf7, 0xe4, 0x00, 255]);
        expect(texel(lut, 7)).toEqual([0xf8, 0x59, 0x00, 255]);
        expect(texel(lut, 9.5)).toEqual([0xd8, 0x00, 0x1d, 255]);
        expect(texel(lut, 13)).toEqual([0x6b, 0x49, 0xc8, 255]);
    });
});

describe('modelled note', () => {
    test('says the values are modelled, not measured', () => {
        expect(MODELLED_NOTE).toBe('Modelled from GFS ozone and cloud cover');
    });
});
