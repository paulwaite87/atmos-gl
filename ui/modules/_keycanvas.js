// ui/modules/_keycanvas.js
/**
 * Client-side replacement for the (removed) backend PlottingMixin.save_key_image():
 * draws a horizontal colourbar legend key -- gradient bar + tick marks/labels + title
 * -- on a <canvas>, from the SAME lookup-table data each layer already uses to colour
 * itself (buildLUT()/_colormaps.js's baked arrays/buildThresholdLUT()/buildSteppedLUT()).
 * See issue #302. Visual style (white text/ticks, transparent background, title above a
 * horizontal bar) matches the removed matplotlib key closely enough to be a drop-in
 * replacement in #legend-stack.
 *
 * A colourbar always spans its norm's domain LINEARLY across its width by construction
 * (that's what makes a colourbar readable) -- so screen fraction t = px/barW IS the
 * colormap position for every norm, linear or not (e.g. TwoSlopeNorm), and the bar can
 * always be drawn by sampling the LUT directly at t. Only TICK placement needs the
 * value -> position mapping (`toPos`), for norms where that isn't the plain linear
 * (v - vmin) / (vmax - vmin) -- see greenhouse_gases.js/sst.js's anomaly-mode callers.
 */

const CSS_WIDTH = 200;
const CSS_HEIGHT = 54;
const BAR_HEIGHT = 14;

function formatTick(value, tickFormat) {
    if (typeof tickFormat === 'function') return tickFormat(value);
    if (tickFormat === '%d') return String(Math.round(value));
    const m = /^%\.(\d)f$/.exec(tickFormat || '');
    if (m) return value.toFixed(Number(m[1]));
    return String(value);
}

// Sample a 256-entry LUT (RGBA stride=4, or RGB stride=3) at fractional position t.
function sampleLut(lut, t, stride) {
    const i = Math.max(0, Math.min(255, Math.round(t * 255)));
    const o = i * stride;
    return [lut[o], lut[o + 1], lut[o + 2], stride === 4 ? lut[o + 3] / 255 : 1];
}

/**
 * Draw the key onto `canvas`. Options:
 *   lut          256-entry Uint8Array colour lookup table (stride 3 or 4).
 *   stride       3 (RGB) or 4 (RGBA, default) -- matches the LUT's own layout.
 *   vmin, vmax   the bar's data-value domain (for the default linear toPos).
 *   ticks        array of data-unit tick values.
 *   title        legend title text.
 *   tickFormat   '%d' / '%.1f' / '%.2f' (matplotlib FormatStrFormatter equivalent),
 *                or a (value) => string function. Omit for String(value).
 *   toPos        optional (value) => 0..1 override for non-linear norms (e.g.
 *                TwoSlopeNorm) -- only affects where TICKS land, not the bar's colour.
 *   decorate     optional (ctx, {barX, barY, barW, barH, toX}) => void, called after
 *                the bar is drawn and before ticks -- for callers that annotate the
 *                bar itself (e.g. waves' below-threshold shading).
 */
export function drawKey(canvas, {
    lut, stride = 4, vmin, vmax, ticks, title, tickFormat, toPos, decorate,
}) {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = CSS_WIDTH * dpr;
    canvas.height = CSS_HEIGHT * dpr;
    canvas.style.width = `${CSS_WIDTH}px`;
    canvas.style.height = `${CSS_HEIGHT}px`;
    canvas.style.display = 'block';

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, CSS_WIDTH, CSS_HEIGHT);

    const barX = 4, barY = 22, barW = CSS_WIDTH - 2 * barX, barH = BAR_HEIGHT;
    const position = toPos || ((v) => (v - vmin) / Math.max(1e-9, vmax - vmin));
    const toX = (v) => barX + Math.max(0, Math.min(1, position(v))) * barW;

    ctx.fillStyle = '#fff';
    ctx.font = 'bold 11px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.fillText(title, CSS_WIDTH / 2, 13);

    for (let px = 0; px < barW; px++) {
        const t = barW > 1 ? px / (barW - 1) : 0;
        const [r, g, b, a] = sampleLut(lut, t, stride);
        ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
        ctx.fillRect(barX + px, barY, 1, barH);
    }

    if (decorate) decorate(ctx, { barX, barY, barW, barH, toX });

    ctx.strokeStyle = '#fff';
    ctx.fillStyle = '#fff';
    ctx.lineWidth = 1;
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const tick of ticks) {
        const x = toX(tick);
        ctx.beginPath();
        ctx.moveTo(x, barY + barH);
        ctx.lineTo(x, barY + barH + 3);
        ctx.stroke();
        ctx.fillText(formatTick(tick, tickFormat), x, barY + barH + 4);
    }
}
