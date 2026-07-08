// ui/modules/_thresholdpalette.js
/**
 * Shared "critical zone" colour-LUT builder behind ozone.js and pwat.js -- mirrors
 * tasks/scalar_field.py's _threshold_colormap() so the animated GPU layer matches the
 * backend's static render. One side of `threshold` grades through `paletteColors`
 * (first colour at the threshold boundary, last at the domain's extreme edge); the
 * other side is flat `flatColor`. `focus: 'below'` grades toward vmin (ozone: worse
 * toward the lowest reading); `focus: 'above'` grades toward vmax (pwat: worse toward
 * the highest reading). A small transition band softens the seam.
 *
 * Colours are [r, g, b] in 0..1 (alpha implied 1) for `paletteColors`, or [r, g, b, a]
 * for `flatColor`. Returns a 256-entry RGBA Uint8Array ready for uploadCmap().
 */
export function buildThresholdLUT({ vmin, vmax, threshold, focus, paletteColors, flatColor }) {
    const span = Math.max(1e-9, vmax - vmin);
    const t = Math.max(0, Math.min(1, (threshold - vmin) / span));
    const band = 0.01;
    const n = paletteColors.length;
    const extremeEdge = focus === 'below' ? 0.0 : 1.0;
    const posAt = (i) => (n === 1 ? t : t + (i / (n - 1)) * (extremeEdge - t));

    const stops = paletteColors.map((c, i) => [posAt(i), c]);
    if (focus === 'below') {
        stops.push([Math.min(1, t + band), flatColor]);
        stops.push([1.0, flatColor]);
    } else {
        stops.push([0.0, flatColor]);
        stops.push([Math.max(0, t - band), flatColor]);
    }
    stops.sort((a, b) => a[0] - b[0]);

    const deduped = [];
    for (const [pos0, c] of stops) {
        let pos = pos0;
        if (deduped.length && pos <= deduped[deduped.length - 1][0]) {
            pos = deduped[deduped.length - 1][0] + 1e-6;
        }
        deduped.push([pos, c]);
    }

    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const x = i / 255;
        let lo = deduped[0];
        let hi = deduped[deduped.length - 1];
        for (let k = 0; k < deduped.length - 1; k++) {
            if (x >= deduped[k][0] && x <= deduped[k + 1][0]) {
                lo = deduped[k]; hi = deduped[k + 1]; break;
            }
        }
        const [loPos, loColor] = lo;
        const [hiPos, hiColor] = hi;
        const f = hiPos > loPos ? (x - loPos) / (hiPos - loPos) : 0;
        const o = i * 4;
        for (let ch = 0; ch < 4; ch++) {
            const a = loColor[ch] ?? (ch === 3 ? 1 : 0);
            const b = hiColor[ch] ?? (ch === 3 ? 1 : 0);
            lut[o + ch] = Math.round((a + (b - a) * f) * 255);
        }
    }
    return lut;
}
