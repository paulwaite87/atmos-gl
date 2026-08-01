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

/**
 * Discrete-band variant of buildThresholdLUT above: below `minValue` (or <= 0) is
 * fully transparent (`flatColor`); at/above it, colour steps through `levels`/
 * `paletteColors` like a meteorological intensity scale (matches precipitation's
 * static PNG BoundaryNorm render + its legend key) -- hard steps, not a gradient.
 *
 * Adjacent LUT texels at a band boundary differ abruptly, but the colour LUT
 * texture already samples with GPU LINEAR filtering (see uploadCmap() in
 * _webglfill.js), so the hardware blends across that single-texel step on its own
 * -- the same mechanism buildThresholdLUT's own transition band already relies on,
 * just with hard interior steps instead of a smooth ramp. No shader-side derivative
 * math (fwidth-based edge AA) is needed at all, which is what made this the right
 * replacement for precipitation.js's old bespoke bandOf()/EDGES/fwidth shader logic
 * -- that custom per-pixel approach was the root cause of a run of rendering bugs
 * (bicubic ringing, then fwidth() noise amplification once the data itself was
 * smoothed, then an unexplained seam), none of which the other LUT-based layers
 * (pwat/ozone/temperature) sharing this exact mechanism have ever hit.
 *
 * `levels` are band boundaries in the SAME units as `vmin`/`vmax` (levels.length-1
 * bands; mirrors precipitation.py's LEVELS); `paletteColors` (7 stops) are
 * interpolated across the bands exactly as the old bandColours() helper did.
 */
export function buildSteppedLUT({ vmin, vmax, minValue, levels, paletteColors, flatColor }) {
    const span = Math.max(1e-9, vmax - vmin);
    const nBands = levels.length - 1;
    const nColors = paletteColors.length;

    const bandColor = (b) => {
        const pos = b / (nBands - 1);                // 0..1 across the ramp
        const fp = pos * (nColors - 1);               // 0..(nColors-1)
        const lo = Math.floor(fp);
        const hi = Math.min(lo + 1, nColors - 1);
        const f = fp - lo;
        return [0, 1, 2].map((ch) => paletteColors[lo][ch] * (1 - f) + paletteColors[hi][ch] * f);
    };
    const bandColors = [];
    for (let b = 0; b < nBands; b++) bandColors.push(bandColor(b));

    const bandOf = (value) => {
        let b = 0;
        for (let k = 0; k < nBands; k++) if (value >= levels[k]) b = k;
        return b;
    };

    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const value = vmin + (i / 255) * span;
        const o = i * 4;
        if (value <= 0 || value < minValue) {
            lut[o] = Math.round((flatColor[0] ?? 0) * 255);
            lut[o + 1] = Math.round((flatColor[1] ?? 0) * 255);
            lut[o + 2] = Math.round((flatColor[2] ?? 0) * 255);
            lut[o + 3] = Math.round((flatColor[3] ?? 0) * 255);
            continue;
        }
        const c = bandColors[bandOf(value)];
        lut[o] = Math.round(c[0] * 255);
        lut[o + 1] = Math.round(c[1] * 255);
        lut[o + 2] = Math.round(c[2] * 255);
        lut[o + 3] = 255;
    }
    return lut;
}
