// ui/modules/_feedhelpers.js
/**
 * Shared fetch/icon/popup-card plumbing behind the six event-feed layers (quakes.js,
 * volcanoes.js, satellites.js, storms.js, lightning.js, shipping.js) -- architecture
 * review candidate "six frontend event-feed modules copy-paste the same load
 * scaffold". mount/refresh/unmount stay bespoke per module (layer count and pulse
 * wiring genuinely vary -- see ADR-0002 for why this repo doesn't force those into a
 * shared shape); this owns only the pieces that were actually duplicated byte-for-byte.
 */

export async function fetchOrThrow(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}

// Icon-array preloader shared by quakes.js/lightning.js/shipping.js -- was
// byte-for-byte identical in lightning.js/shipping.js; quakes.js's copy was missing
// the !res.ok check, silently fixed by unifying onto this one. volcanoes.js's
// single-icon case has its own post-await hasImage re-check (a race-guard this
// three-icon version doesn't need) and stays bespoke.
export async function preloadIcons(map, icons) {
    await Promise.all(icons.map(async (ic) => {
        if (map.hasImage(ic.id)) return;
        const res = await fetch(`${window.location.origin}${ic.url}`);
        if (!res.ok) throw new Error(`Could not load ${ic.id}`);
        map.addImage(ic.id, await createImageBitmap(await res.blob()));
    }));
}

// Card template shared by volcanoes.js/satellites.js/storms.js -- the only three
// with an identical wrapper/hr/row shape (title + hr + "label: value" rows), just
// differing in title color/size and row label width. quakes.js/lightning.js/
// shipping.js's popups diverge enough (a fused title+text line, a computed inline
// color, br-separated multi-column rows) that forcing them through this shape would
// just re-add the per-caller params ADR-0002 already rejected for markers.js -- left
// bespoke.
export function popupCard({ title, titleColor = '#333', titleSize = 13, padding = 4, rows = [] }) {
    const rowsHtml = rows
        .map(({ label, value, width = 45 }) =>
            `<div><span style="color:#666;width:${width}px;display:inline-block;">${label}:</span> <strong>${value}</strong></div>`)
        .join('');
    return `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:${padding}px;">
            <strong style="font-size:${titleSize}px;color:${titleColor};">${title}</strong>
            <hr style="border:0;border-top:1px solid #ccc;margin:4px 0;">
            ${rowsHtml}
        </div>`;
}
