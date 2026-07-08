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

export function showLegend(slotId, url) {
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
