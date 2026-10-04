import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, buildPopupHtml, escapeHtml } from './_feedhelpers.js';

// DeepStateMap.live's front line (collectors/frontline.py): occupied, contested
// ("grey zone") and liberated polygons from DeepState's latest update. Ukrainian-held
// territory has no polygon of its own -- it's everything not shaded here.
const STATUS_LABELS = {
    occupied: 'Occupied by Russia since 2022',
    contested: 'Contested (grey zone)',
    liberated: 'Liberated by Ukraine',
    attack_direction: 'Direction of attack',
};

// DeepState's direction-of-attack points (collectors/frontline.py): one of 16 compass
// bearings each, drawn as our own arrow -- centred on the point, as DeepState draws
// them -- rotated to it.
const ARROW_IMAGE = 'frontline-arrow';
const ARROW_PX = 48;           // drawn at 2x for sharpness; icon-size 0.5 => 24 CSS px
const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

export const compassPoint = (bearing) => COMPASS[Math.round((((Number(bearing) % 360) + 360) % 360) / 22.5) % 16];

export const arrowSize = (cfg) => {
    const size = Number(cfg.arrow_size);
    return Number.isFinite(size) && size > 0 ? size : 1;
};

// An upward-pointing arrow (rotated per feature by icon-rotate), in the occupied red
// with a dark outline so it reads over any of the shading.
function arrowImage() {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = ARROW_PX;
    const ctx = canvas.getContext('2d');
    const c = ARROW_PX / 2;
    ctx.beginPath();
    ctx.moveTo(c, 3);                      // tip
    ctx.lineTo(ARROW_PX - 7, c + 2);       // right barb
    ctx.lineTo(c + 7, c + 2);
    ctx.lineTo(c + 7, ARROW_PX - 3);       // shaft
    ctx.lineTo(c - 7, ARROW_PX - 3);
    ctx.lineTo(c - 7, c + 2);
    ctx.lineTo(7, c + 2);                  // left barb
    ctx.closePath();
    ctx.fillStyle = '#d32f2f';
    ctx.fill();
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = 'rgba(40,0,0,0.85)';
    ctx.stroke();
    return ctx.getImageData(0, 0, ARROW_PX, ARROW_PX);
}

// Crimea, Tuzla and the 2014 parts of Donetsk/Luhansk (collectors/frontline.py's
// occupied_since) -- violet, distinct from the 2022 red and from every other colour here.
const OCCUPIED_2014_COLOR = '#8e24aa';
const OCCUPIED_2014_LABEL = 'Occupied by Russia since 2014';

const STATUS_COLORS = {
    occupied: '#c62828',
    contested: '#9e9e9e',
    liberated: '#2e7d32',
};

// Gains/losses overlay (lib/frontline_changes.py): occupied-area change between
// DeepState's latest update and the one current 1/7/30 days earlier. Drawn on top of
// the shading at full strength so a change stands out against its own status colour.
const CHANGE_LABELS = { russian_gain: 'Russian gain', ukrainian_gain: 'Ukrainian gain' };
const CHANGE_COLORS = { russian_gain: '#ff1744', ukrainian_gain: '#2979ff' };
const CHANGE_TEXT = { russian_gain: 'newly occupied', ukrainian_gain: 'no longer occupied' };
const EMPTY = { type: 'FeatureCollection', features: [] };

const changeColorExpr = [
    'match', ['get', 'change'],
    'russian_gain', CHANGE_COLORS.russian_gain,
    'ukrainian_gain', CHANGE_COLORS.ukrainian_gain,
    '#888888',
];

export const changeDays = (cfg) => {
    const days = Number(cfg.change_days);
    return [1, 7, 30].includes(days) ? days : 7;
};

const formatDate = (iso, withTime = true) => new Date(iso).toLocaleString(undefined, withTime
    ? { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { year: 'numeric', month: 'short', day: 'numeric' });

export function changeSummaryRows(comparison) {
    if (!comparison) return [];
    const km2 = (v) => `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 })} km²`;
    return [
        { label: 'Period', value: `${formatDate(comparison.from.created_at, false)} → ${formatDate(comparison.to.created_at, false)}`, width: 55 },
        { label: 'Russia', value: `+${km2(comparison.totals_km2.russian_gain)}`, width: 55, valueColor: CHANGE_COLORS.russian_gain },
        { label: 'Ukraine', value: `+${km2(comparison.totals_km2.ukrainian_gain)}`, width: 55, valueColor: CHANGE_COLORS.ukrainian_gain },
    ];
}

// The update note's pieces (collectors/frontline.py's description_segments()): plain
// text, a fly-to link (lat/lon/zoom -- DeepState's own map links, re-pointed at this
// map), or an external link (url). Every piece of text is escaped; snapshots stored
// before segments existed fall back to the plain description.
export function descriptionHtml(snapshot) {
    if (!snapshot) return '';
    const segments = snapshot.description_segments;
    if (!Array.isArray(segments)) return escapeHtml(snapshot.description ?? '');
    return segments.map((s) => {
        const text = escapeHtml(s.text);
        if (Number.isFinite(s.lat) && Number.isFinite(s.lon)) {
            const target = [s.lon, s.lat, Number.isFinite(s.zoom) ? s.zoom : 13].join(',');
            return `<a href="#" data-frontline-fly="${target}" title="Show on map">${text}</a>`;
        }
        if (typeof s.url === 'string' && /^https?:\/\//.test(s.url)) {
            return `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${text}</a>`;
        }
        return text;
    }).join('');
}

// Area notes carry bare URLs (DeepState's Telegram posts); link them, escaping the rest.
export function linkifyHtml(text) {
    const parts = String(text ?? '').split(/(https?:\/\/[^\s<>"')]+)/);
    return parts.map((part, i) => (i % 2
        ? `<a href="${escapeHtml(part)}" target="_blank" rel="noopener noreferrer">${escapeHtml(part)}</a>`
        : escapeHtml(part))).join('');
}

// "lon,lat,zoom" from a fly-to link's data attribute, or null if it's malformed.
export function parseFlyTarget(value) {
    const [lon, lat, zoom] = String(value ?? '').split(',').map(Number);
    if (![lon, lat, zoom].every(Number.isFinite)) return null;
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return { center: [lon, lat], zoom };
}

const ATTRIBUTION = '<a href="https://deepstatemap.live/en" target="_blank" rel="noopener noreferrer">DeepStateMap.live</a>';

export function visibleStatuses(cfg) {
    const statuses = ['occupied'];
    if (cfg.show_contested !== false) statuses.push('contested');
    if (cfg.show_liberated === true) statuses.push('liberated');
    return statuses;
}

const filterFor = (cfg) => ['in', ['get', 'status'], ['literal', visibleStatuses(cfg)]];

const colorExpr = [
    'case',
    ['==', ['get', 'occupied_since'], 2014], OCCUPIED_2014_COLOR,
    ['match', ['get', 'status'],
        'occupied', STATUS_COLORS.occupied,
        'contested', STATUS_COLORS.contested,
        'liberated', STATUS_COLORS.liberated,
        '#888888'],
];

// The popup title for an area: 2014-occupied areas get their own label.
export const statusLabel = (properties) => (properties.occupied_since === 2014
    ? OCCUPIED_2014_LABEL
    : STATUS_LABELS[properties.status] || properties.status);

export function loadLayer(map, config) {
    const sourceId = 'frontline-source';
    const fillId = 'frontline-fill';
    const lineId = 'frontline-line';
    const changesSourceId = 'frontline-changes-source';
    const changesFillId = 'frontline-changes-fill';
    const changesLineId = 'frontline-changes-line';
    const arrowsId = 'frontline-arrows';
    let stopPopup = null;
    let snapshot = null;  // which DeepState update is showing (route's "snapshot" member)
    let comparison = null;  // which updates the overlay compares (route's "comparison")

    const fetchData = async () => {
        const data = await fetchOrThrow(`${window.WM_API}/frontline/geojson?t=${Date.now()}`);
        snapshot = data.snapshot || null;
        return data;
    };

    // Only fetched while the overlay is on; otherwise its layers sit empty and hidden.
    const fetchChanges = async (cfg) => {
        if (!cfg.show_changes) {
            comparison = null;
            return EMPTY;
        }
        const data = await fetchOrThrow(
            `${window.WM_API}/frontline/changes?days=${changeDays(cfg)}&t=${Date.now()}`);
        comparison = data.comparison || null;
        return data;
    };

    const opacity = (cfg) => Number(cfg.opacity ?? 45) / 100;
    const visibility = (cfg) => (cfg.show_changes ? 'visible' : 'none');
    const arrowVisibility = (cfg) => (cfg.show_attack_directions === false ? 'none' : 'visible');
    const arrowIconSize = (cfg) => 0.5 * arrowSize(cfg);

    const statusHtml = (f) => {
        const { status, liberated_on: liberatedOn, note, bearing, area_name: areaName } = f.properties;
        const blocks = [];
        if (areaName) {
            blocks.push({ type: 'text', text: areaName, bold: true });
            blocks.push({ type: 'divider' });
        }
        if (status === 'attack_direction') {
            blocks.push({ type: 'rows', rows: [{ label: 'Heading', value: `${compassPoint(bearing)} (${Math.round(bearing)}°)`, width: 60 }] });
            blocks.push({ type: 'divider' });
        }
        if (status === 'liberated' && (liberatedOn || note)) {
            // DeepState's dates are day.month only -- most are from spring 2022.
            if (liberatedOn) blocks.push({ type: 'rows', rows: [{ label: 'Liberated', value: liberatedOn, width: 60 }] });
            if (note) blocks.push({ type: 'text', text: linkifyHtml(note), raw: true });
            blocks.push({ type: 'divider' });
        }
        if (snapshot) {
            blocks.push({ type: 'rows', rows: [{ label: 'Updated', value: formatDate(snapshot.created_at), width: 60 }] });
            const description = descriptionHtml(snapshot);
            if (description) blocks.push({ type: 'text', text: description, raw: true });
        }
        blocks.push({ type: 'notice', raw: true, color: '#6c757d', text: `Source: ${ATTRIBUTION}` });
        return buildPopupHtml({ title: { text: statusLabel(f.properties) }, blocks });
    };

    const changeHtml = (f) => {
        const { change, area_km2: area } = f.properties;
        const blocks = [
            { type: 'text', text: `${area} km² ${CHANGE_TEXT[change] || 'changed'}`, bold: true },
            { type: 'divider' },
            { type: 'text', text: 'Total gains in this period:' },
            { type: 'rows', rows: changeSummaryRows(comparison) },
            { type: 'notice', raw: true, color: '#6c757d', text: `Source: ${ATTRIBUTION}` },
        ];
        return buildPopupHtml({ title: { text: CHANGE_LABELS[change] || change }, blocks });
    };

    const popupHtml = (f) => (f.properties.change ? changeHtml(f) : statusHtml(f));

    // Fly-to links live in popup HTML strings, so one delegated listener on the map
    // container handles them all.
    const onFlyClick = (e) => {
        const link = e.target.closest?.('[data-frontline-fly]');
        if (!link) return;
        e.preventDefault();
        const target = parseFlyTarget(link.dataset.frontlineFly);
        if (!target) return;
        // The popup is anchored where it was opened, which the flight moves away from.
        stopPopup?.close();
        map.flyTo(target);
    };

    const mount = async (cfg) => {
        const [data, changes] = await Promise.all([fetchData(), fetchChanges(cfg)]);
        if (map.getSource(sourceId)) return;          // guard against races
        map.addSource(sourceId, { type: 'geojson', data, attribution: ATTRIBUTION });
        map.addLayer({
            id: fillId, type: 'fill', source: sourceId,
            filter: filterFor(cfg),
            paint: { 'fill-color': colorExpr, 'fill-opacity': opacity(cfg) },
        });
        map.addLayer({
            id: lineId, type: 'line', source: sourceId,
            filter: filterFor(cfg),
            paint: { 'line-color': colorExpr, 'line-width': 1, 'line-opacity': 0.9 },
        });
        // Always added (empty and hidden while off) so the popup can bind to it once.
        map.addSource(changesSourceId, { type: 'geojson', data: changes });
        map.addLayer({
            id: changesFillId, type: 'fill', source: changesSourceId,
            layout: { visibility: visibility(cfg) },
            paint: { 'fill-color': changeColorExpr, 'fill-opacity': 0.75 },
        });
        map.addLayer({
            id: changesLineId, type: 'line', source: changesSourceId,
            layout: { visibility: visibility(cfg) },
            paint: { 'line-color': changeColorExpr, 'line-width': 1.5 },
        });
        // 'move': adjacent polygons share one fill layer, so mouseenter alone wouldn't
        // re-render crossing from one into the next. The changes layer is bound last so
        // its handler runs after the shading's on the same mousemove, and its popup
        // wins where a change sits over shaded territory.
        if (!map.hasImage(ARROW_IMAGE)) map.addImage(ARROW_IMAGE, arrowImage(), { pixelRatio: 2 });
        map.addLayer({
            id: arrowsId, type: 'symbol', source: sourceId,
            filter: ['==', ['get', 'status'], 'attack_direction'],
            layout: {
                visibility: arrowVisibility(cfg),
                'icon-image': ARROW_IMAGE,
                'icon-size': arrowIconSize(cfg),
                'icon-rotate': ['get', 'bearing'],
                'icon-rotation-alignment': 'map',
                'icon-allow-overlap': true,
                'icon-ignore-placement': true,
            },
        });
        // pinOnClick: the popup follows the cursor, so a click pins it in place to let
        // the mouse reach its fly-to and Telegram links. Arrows are bound last so their
        // popup wins over the area beneath them.
        stopPopup = hoverPopup(map, [fillId, changesFillId, arrowsId], {
            html: popupHtml, maxWidth: '320px', event: 'move', pinOnClick: true,
        });
        map.getContainer().addEventListener('click', onFlyClick);
    };

    const refresh = async (cfg) => {
        const [data, changes] = await Promise.all([fetchData(), fetchChanges(cfg)]);
        map.getSource(sourceId)?.setData(data);
        map.getSource(changesSourceId)?.setData(changes);
        for (const id of [fillId, lineId]) {
            if (map.getLayer(id)) map.setFilter(id, filterFor(cfg));
        }
        for (const id of [changesFillId, changesLineId]) {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility(cfg));
        }
        if (map.getLayer(arrowsId)) {
            map.setLayoutProperty(arrowsId, 'visibility', arrowVisibility(cfg));
            map.setLayoutProperty(arrowsId, 'icon-size', arrowIconSize(cfg));
        }
        if (map.getLayer(fillId)) map.setPaintProperty(fillId, 'fill-opacity', opacity(cfg));
    };

    const unmount = () => {
        stopPopup?.();
        map.getContainer().removeEventListener('click', onFlyClick);
        for (const id of [arrowsId, changesLineId, changesFillId, lineId, fillId]) {
            if (map.getLayer(id)) map.removeLayer(id);
        }
        for (const id of [changesSourceId, sourceId]) {
            if (map.getSource(id)) map.removeSource(id);
        }
    };

    return liveDataSync(map, {
        sectionKey: 'frontline', initialConfig: config, mount, refresh, unmount, refreshMs: 15 * 60000,
    });
}
