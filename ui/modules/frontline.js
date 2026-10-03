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
    let stopPopup = null;
    let snapshot = null;  // which DeepState update is showing (route's "snapshot" member)

    const fetchData = async () => {
        const data = await fetchOrThrow(`${window.WM_API}/frontline/geojson?t=${Date.now()}`);
        snapshot = data.snapshot || null;
        return data;
    };

    const opacity = (cfg) => Number(cfg.opacity ?? 45) / 100;

    const popupHtml = (f) => {
        const status = f.properties.status;
        const blocks = [];
        if (snapshot) {
            const when = new Date(snapshot.created_at).toLocaleString(undefined,
                { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
            blocks.push({ type: 'rows', rows: [{ label: 'Updated', value: when, width: 60 }] });
            if (snapshot.description) blocks.push({ type: 'text', text: snapshot.description });
        }
        blocks.push({ type: 'notice', raw: true, color: '#6c757d', text: `Source: ${ATTRIBUTION}` });
        return buildPopupHtml({ title: { text: STATUS_LABELS[status] || status }, blocks });
    };

    const mount = async (cfg) => {
        const data = await fetchData();
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
        // 'move': adjacent polygons share one fill layer, so mouseenter alone wouldn't
        // re-render crossing from one into the next.
        stopPopup = hoverPopup(map, fillId, { html: popupHtml, maxWidth: '320px', event: 'move' });
    };

    const refresh = async (cfg) => {
        const data = await fetchData();
        map.getSource(sourceId)?.setData(data);
        for (const id of [fillId, lineId]) {
            if (map.getLayer(id)) map.setFilter(id, filterFor(cfg));
        }
        if (map.getLayer(fillId)) map.setPaintProperty(fillId, 'fill-opacity', opacity(cfg));
    };

    const unmount = () => {
        stopPopup?.();
        if (map.getLayer(lineId)) map.removeLayer(lineId);
        if (map.getLayer(fillId)) map.removeLayer(fillId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, {
        sectionKey: 'frontline', initialConfig: config, mount, refresh, unmount, refreshMs: 15 * 60000,
    });
}
