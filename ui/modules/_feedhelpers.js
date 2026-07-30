// ui/modules/_feedhelpers.js
/**
 * Shared fetch/icon/popup-card plumbing behind the six event-feed layers (quakes.js,
 * volcanoes.js, satellites.js, storms.js, lightning.js, shipping.js) -- architecture
 * review candidate "six frontend event-feed modules copy-paste the same load
 * scaffold". mount/refresh/unmount stay bespoke per module (layer count and pulse
 * wiring genuinely vary -- see ADR-0002 for why this repo doesn't force those into a
 * shared shape); this owns only the pieces that were actually duplicated byte-for-byte.
 */

// HTML-entity-escape untrusted text before it's interpolated into a popup template
// string handed to maplibregl.Popup.setHTML() -- which parses that string as real
// HTML/DOM, same trust level as innerHTML. Every popup-bearing layer's data ultimately
// comes from an external feed/API (GVP, USGS, NHC/JTWC, Celestrak, and -- critically --
// AIS ShipStaticData and ADS-B, both of which are self-reported by the vessel/aircraft
// transponder with NO validation, so a ship or aircraft's reported name/callsign/
// destination is fully attacker-controlled free text). This is the actual XSS-blocking
// control: correct regardless of what any given collector does or doesn't strip at
// ingest, and unlike tag-stripping it can't be bypassed by a payload that isn't
// tag-shaped (e.g. one relying on & already being unencoded in the stored value).
export function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

export async function fetchOrThrow(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}

// Icon-array preloader shared by quakes.js/lightning.js/shipping.js/flightradar.js --
// was byte-for-byte identical in lightning.js/shipping.js; quakes.js's copy was
// missing the !res.ok check, silently fixed by unifying onto this one. volcanoes.js's
// single-icon case has its own post-await hasImage re-check (a race-guard this
// three-icon version doesn't need) and stays bespoke.
//
// An icon entry with `sdf: true` (flightradar.js's aircraft/glider icons -- a white
// silhouette on transparent) is registered as an SDF image, so a layer can tint it at
// render time via the icon-color paint property instead of needing a separately-baked
// PNG per color. Every other caller's icons are pre-colored PNGs and omit `sdf`,
// which must call addImage with exactly its original two-argument signature -- some
// mocked `map`s in tests assert against that exact call shape.
export async function preloadIcons(map, icons) {
    await Promise.all(icons.map(async (ic) => {
        if (map.hasImage(ic.id)) return;
        const res = await fetch(`${window.location.origin}${ic.url}`);
        if (!res.ok) throw new Error(`Could not load ${ic.id}`);
        const bitmap = await createImageBitmap(await res.blob());
        if (ic.sdf) {
            map.addImage(ic.id, bitmap, { sdf: true });
        } else {
            map.addImage(ic.id, bitmap);
        }
    }));
}

// Card template shared by volcanoes.js/satellites.js/storms.js -- the only three
// with an identical wrapper/hr/row shape (title + hr + "label: value" rows), just
// differing in title color/size and row label width. quakes.js/lightning.js/
// shipping.js's popups diverge enough (a fused title+text line, a computed inline
// color, br-separated multi-column rows) that forcing them through this shape would
// just re-add the per-caller params ADR-0002 already rejected for markers.js -- left
// bespoke.
export function popupCard({ title, titleColor = '#333', titleSize = 13, padding = 4, rows = [], fontSize = 12 }) {
    // Escaped here, not left to each caller: title/label/value all ultimately trace
    // back to an external feed (see escapeHtml's comment) -- escaping unconditionally
    // inside the shared template means no caller (present or future) can forget.
    const rowsHtml = rows
        .map(({ label, value, width = 45 }) =>
            // Label bold+black, value standard-weight+light-grey -- reads better than
            // the reverse (a subdued label next to a bold value drew the eye to the
            // value first, before its label gave it context). Both routed through
            // escapeHtml() -- see this function's top-level comment and escapeHtml's
            // own docstring.
            // min-width, not width: an inline-block box doesn't clip overflowing
            // content, so a bold label wider than `width` (e.g. "Storm category")
            // was painting straight past the box edge -- burying margin-right's gap
            // under the overflow instead of creating one. min-width still aligns
            // short labels at `width`px but lets long ones expand instead of
            // overlapping the value that follows.
            `<div><strong style="min-width:${width}px;display:inline-block;margin-right:6px;">${escapeHtml(label)}:</strong><span style="color:#666;">${escapeHtml(value)}</span></div>`)
        .join('');
    return `<div style="font-family:sans-serif;font-size:${fontSize}px;color:#000;padding:${padding}px;">
            <strong style="font-size:${titleSize}px;color:${titleColor};">${escapeHtml(title)}</strong>
            <hr style="border:0;border-top:1px solid #ccc;margin:4px 0;">
            ${rowsHtml}
        </div>`;
}
