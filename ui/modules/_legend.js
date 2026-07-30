// ui/modules/_legend.js
/**
 * Shared colourbar-key legend plumbing behind sst.js, waves.js, currents.js, ozone.js,
 * precipitation.js, temperature.js, and wind.js -- architecture review candidate "a
 * home for copy-pasted legend/hover-popup plumbing". All of them independently
 * rebuilt the same create/replace/remove-a-slot-inside-#legend-stack mechanic; most
 * also rebuilt the same "_key" filename transform for an <img>-based key. This owns
 * the slot mechanic once via replaceSlot() (callers supply the content, e.g. wind's
 * gradient bar), with showLegend() as the <img>-specific convenience wrapper most
 * callers use.
 */

// The backend writes a colourbar-key image alongside each layer's outfile, named by
// inserting "_key" before the extension (e.g. "sst.png" -> "sst_key.png").
export function keyFilename(outfile) {
    const i = outfile.lastIndexOf('.');
    const base = i !== -1 ? outfile.slice(0, i) : outfile;
    const ext  = i !== -1 ? outfile.slice(i)    : '';
    return `${base}_key${ext}`;
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

// `opacity` (0-1, matching _opacity.js's opacityUniform convention) is the SAME value
// driving the layer's own on-map visibility. A zeroed-out layer's key is pointless
// clutter -- especially with two heatmaps active and one silenced via its opacity
// slider (the motivating case: Fire Risk + Air Quality both in the legend stack) --
// so an explicit 0 hides the slot instead of drawing an orphaned key for nothing.
// Defaults to always-shown for callers that don't have a single opacity value
// governing the whole layer's visibility (e.g. currents/jetstream, where particles
// driven by the same colour scale can stay visible independent of a fill's opacity).
export function showLegend(slotId, url, opacity = 1) {
    if (opacity <= 0) { removeLegend(slotId); return; }
    replaceSlot(slotId, (slot) => {
        const img = document.createElement('img');
        img.src = url;
        img.style.display = 'block'; img.style.width = '100%';
        slot.appendChild(img);
    });
}

export function removeLegend(slotId) {
    document.getElementById(slotId)?.remove();
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
