// ui/modules/_legend.js
/**
 * Shared colourbar-key legend plumbing behind sst.js, waves.js, currents.js, ozone.js,
 * precipitation.js, temperature.js, and wind.js -- architecture review candidate "a
 * home for copy-pasted legend/hover-popup plumbing". All of them independently
 * rebuilt the same create/replace/remove-a-slot-inside-#legend-stack mechanic. This
 * owns the slot mechanic once via replaceSlot(), with standardLegend() as the
 * <canvas>-key convenience wrapper most callers use (see _keycanvas.js's drawKey()).
 *
 * Legend keys render entirely client-side now (issue #302) -- the backend no longer
 * writes a "_key.png" companion image at all, so there is nothing left to fetch.
 */

import { opacityUniform } from './_opacity.js';
import { drawKey } from './_keycanvas.js';

// Insert `suffix` immediately before a filename's extension: ("sst.png", "_anomaly")
// -> "sst_anomaly.png". Used by callers that insert their own variant suffix into
// their LAYER IMAGE's filename (sst.js's mode, air_quality.js's variable,
// greenhouse_gases.js's species+mode) -- those layers still fetch a server-rendered
// PNG for the image itself; only the legend key moved client-side.
export function insertBeforeExtension(filename, suffix) {
    const i = filename.lastIndexOf('.');
    const base = i !== -1 ? filename.slice(0, i) : filename;
    const ext  = i !== -1 ? filename.slice(i)    : '';
    return `${base}${suffix}${ext}`;
}

export function replaceSlot(slotId, populate) {
    const stack = document.getElementById('legend-stack');
    if (!stack) return;
    document.getElementById(slotId)?.remove();
    const slot = document.createElement('div');
    slot.id = slotId; slot.className = 'legend-slot';
    populate(slot);
    stack.appendChild(slot);
}

export function removeLegend(slotId) {
    document.getElementById(slotId)?.remove();
}

// The replaceSlot+opacityUniform wiring every layer module rebuilt independently
// (architecture review candidate "promote a standardLegend() helper" -- 12 near-
// identical copies). `renderKeyFor(cfg)` returns the drawKey() options (lut/vmin/
// vmax/ticks/title/...) for the current config -- see _keycanvas.js.
//
// `opacityFallback`: pass a number and the key hides when cfg.opacity resolves to 0
// (opacityUniform's honour-explicit-0 behaviour) -- the SAME value driving the
// layer's own on-map visibility, so a zeroed-out layer's key doesn't clutter the
// stack (motivating case: Fire Risk + Air Quality both in the legend stack, one
// silenced via its opacity slider). Omit it entirely (undefined) for currents.js/
// jetstream.js's documented exception -- particles stay visible independent of the
// fill's own opacity, so the key must never hide on opacity=0 either.
export function standardLegend(slotId, renderKeyFor, opacityFallback) {
    const addLegend = (cfg) => {
        if (opacityFallback !== undefined && opacityUniform(cfg, opacityFallback) <= 0) {
            removeLegend(slotId);
            return;
        }
        replaceSlot(slotId, (slot) => {
            const canvas = document.createElement('canvas');
            slot.appendChild(canvas);
            drawKey(canvas, renderKeyFor(cfg));
        });
    };
    return { addLegend, removeLegend: () => removeLegend(slotId) };
}

const LEGENDS_HIDDEN_KEY = 'atmosgl.legendsHidden';

function applyLegendsHiddenState(stack, toggleBtn, hidden) {
    stack.classList.toggle('legends-hidden', hidden);
    toggleBtn.textContent = hidden ? '+' : '−';
    toggleBtn.title = hidden ? 'Show legends' : 'Hide legends';
}

// Single global show/hide-all control for every legend key -- deliberately ONE
// control for the whole stack, not a per-slot toggle (individual per-legend hiding
// was considered and explicitly rejected: one control is simpler and was what was
// actually wanted). Inserted as the first child of #legend-stack -- a sibling of the
// .legend-slot elements replaceSlot() creates/destroys, never itself removed -- so
// the hidden/shown state naturally survives any individual slot being rebuilt (a new
// forecast hour, a palette change, etc.): the state lives as a CSS class
// (legends-hidden) on the never-destroyed stack container, and the
// `.legend-slot { display: none }` rule cascades to any slot regardless of when it
// was added, with no per-slot bookkeeping needed at all. Persisted via localStorage
// so the preference survives a page reload.
export function initLegendToggle() {
    const stack = document.getElementById('legend-stack');
    if (!stack || document.getElementById('legend-toggle')) return;

    const toggleBtn = document.createElement('button');
    toggleBtn.id = 'legend-toggle';
    toggleBtn.type = 'button';

    const hidden = localStorage.getItem(LEGENDS_HIDDEN_KEY) === 'true';
    applyLegendsHiddenState(stack, toggleBtn, hidden);

    toggleBtn.addEventListener('click', () => {
        const nowHidden = !stack.classList.contains('legends-hidden');
        applyLegendsHiddenState(stack, toggleBtn, nowHidden);
        localStorage.setItem(LEGENDS_HIDDEN_KEY, String(nowHidden));
    });

    stack.insertBefore(toggleBtn, stack.firstChild);
}
