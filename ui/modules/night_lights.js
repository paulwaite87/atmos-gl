import { liveLayerSync } from './_refresh.js';
import { replaceSlot, removeLegend } from './_legend.js';

export const CREDIT = 'NASA Black Marble / GIBS, VIIRS Suomi NPP';

// Mirrors the server ramp in tasks/night_lights.py: faint -> deep orange, bright -> near-white.
export const RAMP_STOPS = [
    { at: 0, color: '#cd4b0a' },
    { at: 55, color: '#ffbe32' },
    { at: 100, color: '#fffaeb' },
];

const DEFAULT_OPACITY = 85;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function formatImageDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    if (!m) return '';
    return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

export function legendTitle(iso) {
    const d = formatImageDate(iso);
    return d ? `Night lights · ${d}` : 'Night lights';
}

export function rampGradient() {
    return `linear-gradient(to right, ${RAMP_STOPS.map((s) => `${s.color} ${s.at}%`).join(', ')})`;
}

// opacity is on the standardised 0-100 UI scale; raster-opacity wants 0-1.
export function opacityFor(cfg) {
    const raw = cfg && Number.isFinite(Number(cfg.opacity)) && cfg.opacity !== null ? Number(cfg.opacity) : DEFAULT_OPACITY;
    return Math.max(0, Math.min(100, raw)) / 100;
}

const LEGEND_SLOT = 'night-lights-legend-slot';

function addLegend(date) {
    replaceSlot(LEGEND_SLOT, (slot) => {
        const title = document.createElement('div');
        title.textContent = legendTitle(date);
        title.style.cssText = 'font-size:11px;font-weight:600;color:#eee;margin-bottom:3px;';
        const bar = document.createElement('div');
        bar.style.cssText = `width:160px;height:10px;border-radius:2px;background:${rampGradient()};`;
        const ends = document.createElement('div');
        ends.style.cssText = 'display:flex;justify-content:space-between;font-size:10px;color:#ddd;';
        ends.append(Object.assign(document.createElement('span'), { textContent: 'faint' }),
                    Object.assign(document.createElement('span'), { textContent: 'bright' }));
        const credit = document.createElement('div');
        credit.textContent = CREDIT;
        credit.style.cssText = 'font-size:9px;color:#aaa;margin-top:2px;max-width:200px;line-height:1.25;';
        slot.append(title, bar, ends, credit);
    });
}

export function loadLayer(map, config) {
    const sourceId = 'night-lights-source';
    const layerId = 'night-lights-layer';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${cfg.outfile}`;

    // The sidecar the render task writes next to the texture; a missing/garbled one just
    // leaves the legend title without a date rather than blocking the layer.
    const refreshLegend = () => fetch(`${window.MAP_UI}/data/night_lights_meta.json?t=${Date.now()}`)
        .then((r) => (r.ok ? r.json() : {}))
        .catch(() => ({}))
        .then((meta) => addLegend(meta && meta.date));

    const mount = (cfg) => {
        if (map.getSource(sourceId)) return;
        map.addSource(sourceId, {
            type: 'image',
            url: `${urlFor(cfg)}?t=${Date.now()}`,
            coordinates,
        });
        map.addLayer({
            id: layerId, type: 'raster', source: sourceId,
            paint: { 'raster-opacity': opacityFor(cfg), 'raster-fade-duration': 0 },
        });
        refreshLegend();
    };

    const refresh = (cfg) => {
        const s = map.getSource(sourceId);
        if (s) s.updateImage({ url: `${urlFor(cfg)}?t=${Date.now()}` });
        if (map.getLayer(layerId)) map.setPaintProperty(layerId, 'raster-opacity', opacityFor(cfg));
        refreshLegend();
    };

    const unmount = () => {
        if (map.getLayer(layerId)) map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
        removeLegend(LEGEND_SLOT);
    };

    return liveLayerSync(map, {
        sectionKey: 'night_lights', initialConfig: config,
        mount, refresh, unmount, imageUrl: urlFor,
    });
}
