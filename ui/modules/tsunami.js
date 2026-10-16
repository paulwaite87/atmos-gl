// Tsunami Warnings -- a Point-feed layer: every recent NOAA tsunami warning centre event is
// a badge at its epicentre, the bulletin category as BOTH colour and silhouette so severity
// isn't colour-only (and unlike the GDACS badges, which carry hazard letters). Clicking a
// badge shades the NWS forecast zones the bulletin names; clicking elsewhere clears them.
// Everything is read from our own /api/tsunami routes -- the browser never calls NOAA.
import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, escapeHtml, buildPopupHtml } from './_feedhelpers.js';

export const ATTRIBUTION =
    'Source: NOAA / NWS Pacific and National Tsunami Warning Centers · zones simplified';
export const DISCLAIMER =
    'Terrascope is not an official warning channel: follow your local authorities.';

const NEUTRAL = { shape: 'circle', fill: '#7f8c8d' };
const CATEGORY_STYLE = {
    Warning: { shape: 'octagon', fill: '#d62828' },
    Watch: { shape: 'diamond', fill: '#f39c12' },
    Advisory: { shape: 'triangle', fill: '#f1c40f' },
    Information: NEUTRAL,
};
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MAX_NOTE_CHARS = 240;

export function badgeSpec(category) {
    const style = CATEGORY_STYLE[category] || NEUTRAL;
    return { shape: style.shape, fill: style.fill };
}

export function badgeImageId(category) {
    return `tsunami-${category}`;
}

export function zoneColor(category) {
    return badgeSpec(category).fill;
}

const pad = (n) => String(n).padStart(2, '0');

function formatTime(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()} `
        + `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

// Only ever link out to tsunami.gov itself: bulletin_url comes from an external feed. The
// feed gives plain http; the site is served over https.
function bulletinLinkHtml(url) {
    if (typeof url !== 'string' || !/^https?:\/\/www\.tsunami\.gov\//.test(url)) return '';
    const secure = url.replace(/^http:/, 'https:');
    return `<a href="${escapeHtml(secure)}" target="_blank" rel="noopener noreferrer">tsunami.gov bulletin</a>`;
}

function shortNote(note) {
    if (!note) return '';
    return note.length > MAX_NOTE_CHARS ? `${note.slice(0, MAX_NOTE_CHARS).trimEnd()}…` : note;
}

export function popupHtml(feature) {
    const d = feature.properties;
    const rows = [{ label: 'Category', value: d.category, valueColor: zoneColor(d.category), width: 70 }];
    if (d.magnitude != null) {
        rows.push({ label: 'Magnitude', value: `${d.magnitude_type || ''} ${Number(d.magnitude).toFixed(1)}`.trim(), width: 70 });
    }
    if (d.depth_km != null) rows.push({ label: 'Depth', value: `${Math.round(d.depth_km)} km`, width: 70 });
    if (formatTime(d.origin_time)) rows.push({ label: 'Origin', value: formatTime(d.origin_time), width: 70 });
    if (formatTime(d.expires)) rows.push({ label: 'Expires', value: formatTime(d.expires), width: 70 });
    const note = shortNote(d.note);
    if (note) rows.push({ label: 'Note', value: note, width: 70 });
    const link = bulletinLinkHtml(d.bulletin_url);
    if (link) rows.push({ label: 'Bulletin', value: link, raw: true, width: 70 });
    return buildPopupHtml({
        title: { text: d.location_name || 'Tsunami bulletin', variant: 'alert' },
        subtitle: escapeHtml(`Tsunami ${d.category} · Bulletin ${d.bulletin_number}`),
        blocks: [
            { type: 'divider' },
            { type: 'rows', rows },
            { type: 'notice', text: ATTRIBUTION, color: '#888' },
            { type: 'notice', text: DISCLAIMER, color: '#888' },
        ],
        fontSize: 12,
    });
}

// ---- Footprints (shaded NWS zones) -------------------------------------------------

// Stamps each zone with the colour the map layers read, without mutating the response.
export function styleFootprint(collection) {
    return {
        ...collection,
        features: (collection.features || []).map((f) => ({
            ...f,
            properties: { ...f.properties, color: zoneColor(f.properties && f.properties.category) },
        })),
    };
}

const EMPTY = { type: 'FeatureCollection', features: [] };

// Filter for the highlight ring: matches only the selected event's badge (and nothing at
// all when no event is selected -- no real event has an empty id).
export function selectionFilter(selection) {
    if (!selection) return ['==', ['get', 'event_id'], ''];
    return [
        'all',
        ['==', ['get', 'centre'], selection.centre],
        ['==', ['get', 'event_id'], selection.event_id],
    ];
}

// One selected footprint at a time. A response for a superseded selection (or one that
// lands after clear()) is dropped so a slow request can't redraw over a newer choice.
// `onSelect` (optional) is told the selected {centre, event_id} once its footprint is
// drawn (even an empty one: most events name no zones), and null when cleared -- it drives
// the highlight ring on the selected badge.
export function createFootprintSelector({ apiBase, fetchJson, setData, onSelect }) {
    let seq = 0;
    return {
        async select({ centre, event_id }) {
            const mine = ++seq;
            try {
                const data = await fetchJson(`${apiBase}/tsunami/${centre}/${event_id}/footprint`);
                if (mine !== seq) return;
                setData(styleFootprint(data));
                onSelect?.({ centre, event_id });
            } catch (err) {
                console.warn(`[tsunami] footprint for ${centre}/${event_id} failed`, err);
            }
        },
        clear() {
            seq++;
            setData(EMPTY);
            onSelect?.(null);
        },
    };
}

// ---- Marker badges (drawn, not hotlinked) -------------------------------------------

const BADGE_PX = 44;

function tracePolygon(ctx, sides, radius, rotation) {
    ctx.beginPath();
    for (let i = 0; i < sides; i++) {
        const a = rotation + (i * 2 * Math.PI) / sides;
        const x = BADGE_PX / 2 + radius * Math.cos(a);
        const y = BADGE_PX / 2 + radius * Math.sin(a);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
}

function drawBadge({ shape, fill }) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = BADGE_PX;
    const ctx = canvas.getContext('2d');
    const r = BADGE_PX / 2 - 3;
    if (shape === 'diamond') tracePolygon(ctx, 4, r + 2, 0);
    else if (shape === 'octagon') tracePolygon(ctx, 8, r + 1, Math.PI / 8);
    else if (shape === 'triangle') tracePolygon(ctx, 3, r + 3, -Math.PI / 2);
    else { ctx.beginPath(); ctx.arc(BADGE_PX / 2, BADGE_PX / 2, r, 0, 2 * Math.PI); }
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = '#ffffff';
    ctx.stroke();
    ctx.fillStyle = shape === 'triangle' ? '#333333' : '#ffffff';
    ctx.font = 'bold 14px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('TS', BADGE_PX / 2, BADGE_PX / 2 + (shape === 'triangle' ? 5 : 1));
    return ctx.getImageData(0, 0, BADGE_PX, BADGE_PX);
}

function registerBadges(map) {
    for (const category of Object.keys(CATEGORY_STYLE)) {
        const id = badgeImageId(category);
        if (!map.hasImage(id)) map.addImage(id, drawBadge(badgeSpec(category)), { pixelRatio: 2 });
    }
}

// ---- Layer ------------------------------------------------------------------------

export function loadLayer(map, config) {
    const sourceId = 'tsunami-source';
    const layerId = 'tsunami-layer';
    const footprintSourceId = 'tsunami-footprint-source';
    const fillLayerId = 'tsunami-footprint-fill';
    const lineLayerId = 'tsunami-footprint-line';
    const selectedLayerId = 'tsunami-selected';
    let stopPopup = null;

    const urlFor = (cfg) => `${window.WM_API}/tsunami/geojson`
        + `?grace_days=${cfg.grace_days ?? 1}&t=${Date.now()}`;
    const fetchData = (cfg) => fetchOrThrow(urlFor(cfg));

    const selector = createFootprintSelector({
        apiBase: window.WM_API,
        fetchJson: fetchOrThrow,
        setData: (data) => map.getSource(footprintSourceId)?.setData(data),
        onSelect: (selection) => {
            if (map.getLayer(selectedLayerId)) map.setFilter(selectedLayerId, selectionFilter(selection));
        },
    });
    const onMarkerClick = (e) => {
        if (e.features && e.features.length) selector.select(e.features[0].properties);
    };
    const onMapClick = (e) => {
        if (!map.queryRenderedFeatures(e.point, { layers: [layerId] }).length) selector.clear();
    };

    const mount = async (cfg) => {
        registerBadges(map);
        const data = await fetchData(cfg);
        if (map.getSource(sourceId)) return;          // guard against races
        // Zones go under the badges so a badge is never hidden by its own shading.
        map.addSource(footprintSourceId, { type: 'geojson', data: EMPTY });
        map.addLayer({
            id: fillLayerId, type: 'fill', source: footprintSourceId,
            paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.35 },
        });
        map.addLayer({
            id: lineLayerId, type: 'line', source: footprintSourceId,
            paint: { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-opacity': 0.9 },
        });
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId,
            layout: {
                'icon-image': ['concat', 'tsunami-', ['get', 'category']],
                'icon-size': cfg.icon_zoom ?? 1.0,
                'icon-allow-overlap': true, 'icon-ignore-placement': true,
                // More severe badges draw last, i.e. on top.
                'symbol-sort-key': ['match', ['get', 'category'], 'Warning', 3, 'Watch', 2, 'Advisory', 1, 0],
            },
        });
        map.addLayer({
            id: selectedLayerId, type: 'circle', source: sourceId,
            filter: selectionFilter(null),
            paint: {
                'circle-radius': 19 * (cfg.icon_zoom ?? 1.0),
                'circle-color': 'rgba(0,0,0,0)',
                'circle-stroke-color': '#ffffff',
                'circle-stroke-width': 3,
            },
        });
        stopPopup = hoverPopup(map, layerId, { html: popupHtml, maxWidth: '300px' });
        map.on('click', layerId, onMarkerClick);
        map.on('click', onMapClick);
    };

    const refresh = async (cfg) => {
        const data = await fetchData(cfg);
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        stopPopup?.();
        map.off('click', layerId, onMarkerClick);
        map.off('click', onMapClick);
        for (const id of [selectedLayerId, layerId, lineLayerId, fillLayerId]) {
            if (map.getLayer(id)) map.removeLayer(id);
        }
        for (const id of [sourceId, footprintSourceId]) {
            if (map.getSource(id)) map.removeSource(id);
        }
    };

    return liveDataSync(map, {
        sectionKey: 'tsunami', initialConfig: config, mount, refresh, unmount, refreshMs: 60000,
    });
}
