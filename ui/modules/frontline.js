import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, buildPopupHtml } from './_feedhelpers.js';

// DeepStateMap.live's front line (collectors/frontline.py): occupied, contested
// ("grey zone") and liberated polygons from DeepState's latest update. Ukrainian-held
// territory has no polygon of its own -- it's everything not shaded here.
const STATUS_LABELS = {
    occupied: 'Occupied by Russia',
    contested: 'Contested (grey zone)',
    liberated: 'Liberated by Ukraine',
};

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

const ATTRIBUTION = '<a href="https://deepstatemap.live/en" target="_blank" rel="noopener noreferrer">DeepStateMap.live</a>';

export function visibleStatuses(cfg) {
    const statuses = ['occupied'];
    if (cfg.show_contested !== false) statuses.push('contested');
    if (cfg.show_liberated === true) statuses.push('liberated');
    return statuses;
}

const filterFor = (cfg) => ['in', ['get', 'status'], ['literal', visibleStatuses(cfg)]];

const colorExpr = [
    'match', ['get', 'status'],
    'occupied', STATUS_COLORS.occupied,
    'contested', STATUS_COLORS.contested,
    'liberated', STATUS_COLORS.liberated,
    '#888888',
];

export function loadLayer(map, config) {
    const sourceId = 'frontline-source';
    const fillId = 'frontline-fill';
    const lineId = 'frontline-line';
    const changesSourceId = 'frontline-changes-source';
    const changesFillId = 'frontline-changes-fill';
    const changesLineId = 'frontline-changes-line';
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

    const statusHtml = (f) => {
        const status = f.properties.status;
        const blocks = [];
        if (snapshot) {
            blocks.push({ type: 'rows', rows: [{ label: 'Updated', value: formatDate(snapshot.created_at), width: 60 }] });
            if (snapshot.description) blocks.push({ type: 'text', text: snapshot.description });
        }
        blocks.push({ type: 'notice', raw: true, color: '#6c757d', text: `Source: ${ATTRIBUTION}` });
        return buildPopupHtml({ title: { text: STATUS_LABELS[status] || status }, blocks });
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
        stopPopup = hoverPopup(map, [fillId, changesFillId], { html: popupHtml, maxWidth: '320px', event: 'move' });
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
        if (map.getLayer(fillId)) map.setPaintProperty(fillId, 'fill-opacity', opacity(cfg));
    };

    const unmount = () => {
        stopPopup?.();
        for (const id of [changesLineId, changesFillId, lineId, fillId]) {
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
