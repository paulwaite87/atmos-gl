/**
 * Basemap styles + a small picker control.
 *
 * The basemap is just a MapTiler style URL fed to MapLibre. Switching basemap = fetching
 * a different style and calling map.setStyle(). Because setStyle() wipes ALL layers and
 * sources (including our custom WebGL data layers), the host (index.html) is responsible
 * for tearing down the data layers before the swap and re-mounting them on 'style.load'.
 * This module only deals with (a) the catalog of styles, (b) building a style object with
 * a guaranteed glyphs endpoint, and (c) the picker UI that calls a supplied onSelect.
 */

// slug -> label. The slug goes straight into maps/<slug>/style.json.
export const BASEMAP_STYLES = [
    { slug: 'satellite',     label: 'Satellite' },
    { slug: 'hybrid',        label: 'Satellite + Labels' },
    { slug: 'streets-v2',    label: 'Streets' },
    { slug: 'outdoor-v2',    label: 'Outdoor / Terrain' },
    { slug: 'topo-v2',       label: 'Topographic' },
    { slug: 'dataviz-dark',  label: 'Dataviz Dark' },
    { slug: 'winter',        label: 'Winter' },
    { slug: 'basic-v2',      label: 'Basic' },
];

const styleUrlFor = (slug, key) =>
    `https://api.maptiler.com/maps/${slug}/style.json?key=${key}`;

/**
 * Build a style for MapLibre. Fetches the style JSON and guarantees a glyphs endpoint
 * (some styles omit it, which breaks text-label layers like place markers). Falls back
 * to the bare URL string if the fetch fails — MapLibre can still load from the URL.
 */
export async function buildBasemapStyle(slug, key) {
    const url = styleUrlFor(slug, key);
    try {
        const styleJson = await (await fetch(url)).json();
        if (!styleJson.glyphs) {
            styleJson.glyphs = `https://api.maptiler.com/fonts/{fontstack}/{range}.pbf?key=${key}`;
        }
        return styleJson;
    } catch (err) {
        console.warn(`[basemap] could not pre-fetch style "${slug}"; using URL as-is.`, err);
        return url;
    }
}

/**
 * Render a small basemap picker into `container`. Calls onSelect(slug) when the user
 * picks a different basemap. `current` is the initially-selected slug.
 */
export function mountBasemapPicker(container, current, onSelect) {
    const wrap = document.createElement('div');
    wrap.className = 'basemap-picker';
    const label = document.createElement('label');
    label.textContent = 'Basemap';
    label.className = 'basemap-picker__label';
    const select = document.createElement('select');
    select.className = 'basemap-picker__select';
    for (const { slug, label: text } of BASEMAP_STYLES) {
        const opt = document.createElement('option');
        opt.value = slug; opt.textContent = text;
        if (slug === current) opt.selected = true;
        select.appendChild(opt);
    }
    let busy = false;
    select.addEventListener('change', async () => {
        if (busy) return;
        busy = true; select.disabled = true;
        try { await onSelect(select.value); }
        finally { busy = false; select.disabled = false; }
    });
    wrap.appendChild(label);
    wrap.appendChild(select);
    container.appendChild(wrap);
    return select;
}
