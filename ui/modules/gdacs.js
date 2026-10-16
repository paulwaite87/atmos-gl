// GDACS Alerts (Global Disaster Alert and Coordination System) -- a Point-feed layer:
// every current GDACS event is a badge marker (hazard type letters, alert level as BOTH
// colour and silhouette so severity isn't colour-only). Clicking a marker draws that
// event's stored impact footprint (areas, tracks, cones); clicking elsewhere clears it.
// Everything is read from our own /api/gdacs routes -- the browser never calls GDACS.
import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, escapeHtml, buildPopupHtml } from './_feedhelpers.js';

export const ATTRIBUTION =
    'Source: GDACS (European Commission JRC) · indicative only · geometry simplified';

const NEUTRAL = '#888888';
const ALERT_STYLE = {
    Green: { shape: 'circle', fill: '#2e9e5b' },
    Orange: { shape: 'diamond', fill: '#f39c12' },
    Red: { shape: 'octagon', fill: '#d62828' },
};
const EVENT_TYPES = ['EQ', 'TC', 'FL', 'VO', 'DR', 'WF'];
const TYPE_NAMES = {
    EQ: 'Earthquake', TC: 'Tropical cyclone', FL: 'Flood', VO: 'Volcano', DR: 'Drought', WF: 'Wildfire',
};
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function badgeSpec(eventType, alertLevel) {
    const style = ALERT_STYLE[alertLevel] || { shape: 'circle', fill: NEUTRAL };
    return { shape: style.shape, fill: style.fill, code: eventType };
}

export function badgeImageId(eventType, alertLevel) {
    return `gdacs-${eventType}-${alertLevel}`;
}

function formatDate(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

// Only ever link out to GDACS itself: report_url comes from an external feed.
function reportLinkHtml(url) {
    if (typeof url !== 'string' || !url.startsWith('https://www.gdacs.org/')) return '';
    return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">GDACS report</a>`;
}

export function popupHtml(feature) {
    const d = feature.properties;
    const alertColor = (ALERT_STYLE[d.alert_level] || {}).fill || NEUTRAL;
    const start = formatDate(d.from_date);
    const end = d.is_current ? 'ongoing' : formatDate(d.to_date);
    const rows = [{ label: 'Alert', value: d.alert_level, valueColor: alertColor, width: 60 }];
    if (d.severity_text) rows.push({ label: 'Severity', value: d.severity_text, width: 60 });
    if (d.country) rows.push({ label: 'Country', value: d.country, width: 60 });
    if (start) rows.push({ label: 'Period', value: end ? `${start} – ${end}` : start, width: 60 });
    const link = reportLinkHtml(d.report_url);
    if (link) rows.push({ label: 'Report', value: link, raw: true, width: 60 });
    return buildPopupHtml({
        title: { text: d.name || TYPE_NAMES[d.event_type] || 'GDACS event', variant: 'alert' },
        subtitle: escapeHtml(`${TYPE_NAMES[d.event_type] || d.event_type} · ${d.alert_level} alert`),
        blocks: [
            { type: 'divider' },
            { type: 'rows', rows },
            { type: 'notice', text: ATTRIBUTION, color: '#888' },
        ],
        fontSize: 12,
    });
}

// ---- Footprints -------------------------------------------------------------------

export function classColor(featureClass) {
    const c = String(featureClass || '');
    if (c === 'Poly_Green') return ALERT_STYLE.Green.fill;
    if (c === 'Poly_Orange') return ALERT_STYLE.Orange.fill;
    if (c === 'Poly_Red') return ALERT_STYLE.Red.fill;
    if (c.startsWith('Line')) return '#ffffff';
    if (c === 'Poly_Affected') return '#1f77b4';
    if (c === 'Poly_Global') return '#7f8c8d';
    if (c === 'Poly_area') return '#e4572e';
    if (c === 'Poly_Circle') return '#c0392b';
    if (c.startsWith('Poly_SMPInt')) return '#e1b12c';
    if (c === 'Poly_Cones') return '#8e44ad';
    if (c.startsWith('Point_Polygon')) return '#bdc3c7';
    return NEUTRAL;
}

// Stamps each footprint feature with the colour and draw kind (fill vs line) the map
// layers read, without mutating the response.
export function styleFootprint(collection) {
    return {
        ...collection,
        features: (collection.features || []).map((f) => ({
            ...f,
            properties: {
                ...f.properties,
                color: classColor(f.properties && f.properties.feature_class),
                kind: String(f.geometry && f.geometry.type).includes('Line') ? 'line' : 'fill',
            },
        })),
    };
}

const EMPTY = { type: 'FeatureCollection', features: [] };

// One selected footprint at a time. A response for a superseded selection (or one that
// lands after clear()) is dropped so a slow request can't redraw over a newer choice.
export function createFootprintSelector({ apiBase, fetchJson, setData }) {
    let seq = 0;
    return {
        async select({ event_type, event_id }) {
            const mine = ++seq;
            try {
                const data = await fetchJson(`${apiBase}/gdacs/${event_type}/${event_id}/footprint`);
                if (mine !== seq) return;
                setData(styleFootprint(data));
            } catch (err) {
                console.warn(`[gdacs] footprint for ${event_type}/${event_id} failed`, err);
            }
        },
        clear() {
            seq++;
            setData(EMPTY);
        },
    };
}

// ---- Marker badges (drawn, not hotlinked from GDACS) ---------------------------------

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

function drawBadge({ shape, fill, code }) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = BADGE_PX;
    const ctx = canvas.getContext('2d');
    const r = BADGE_PX / 2 - 3;
    if (shape === 'diamond') tracePolygon(ctx, 4, r + 2, 0);
    else if (shape === 'octagon') tracePolygon(ctx, 8, r + 1, Math.PI / 8);
    else { ctx.beginPath(); ctx.arc(BADGE_PX / 2, BADGE_PX / 2, r, 0, 2 * Math.PI); }
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = '#ffffff';
    ctx.stroke();
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 15px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(code, BADGE_PX / 2, BADGE_PX / 2 + 1);
    return ctx.getImageData(0, 0, BADGE_PX, BADGE_PX);
}

function registerBadges(map) {
    for (const type of EVENT_TYPES) {
        for (const level of Object.keys(ALERT_STYLE)) {
            const id = badgeImageId(type, level);
            if (!map.hasImage(id)) map.addImage(id, drawBadge(badgeSpec(type, level)), { pixelRatio: 2 });
        }
    }
}

// ---- Layer ------------------------------------------------------------------------

export function loadLayer(map, config) {
    const sourceId = 'gdacs-source';
    const layerId = 'gdacs-layer';
    const footprintSourceId = 'gdacs-footprint-source';
    const fillLayerId = 'gdacs-footprint-fill';
    const lineLayerId = 'gdacs-footprint-line';
    let stopPopup = null;

    const urlFor = (cfg) => `${window.WM_API}/gdacs/geojson`
        + `?grace_days=${cfg.grace_days ?? 7}&t=${Date.now()}`;
    const fetchData = (cfg) => fetchOrThrow(urlFor(cfg));

    const selector = createFootprintSelector({
        apiBase: window.WM_API,
        fetchJson: fetchOrThrow,
        setData: (data) => map.getSource(footprintSourceId)?.setData(data),
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
        // Footprints go under the markers so a badge is never hidden by its own shape.
        map.addSource(footprintSourceId, { type: 'geojson', data: EMPTY });
        map.addLayer({
            id: fillLayerId, type: 'fill', source: footprintSourceId,
            filter: ['==', ['get', 'kind'], 'fill'],
            paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.22 },
        });
        map.addLayer({
            id: lineLayerId, type: 'line', source: footprintSourceId,
            paint: { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-opacity': 0.9 },
        });
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId,
            layout: {
                'icon-image': ['concat', 'gdacs-', ['get', 'event_type'], '-', ['get', 'alert_level']],
                'icon-size': cfg.icon_zoom ?? 1.0,
                'icon-allow-overlap': true, 'icon-ignore-placement': true,
                // Higher-scoring (more severe) badges draw last, i.e. on top.
                'symbol-sort-key': ['get', 'alert_score'],
            },
        });
        stopPopup = hoverPopup(map, layerId, { html: popupHtml, pinOnClick: true, maxWidth: '300px' });
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
        for (const id of [layerId, lineLayerId, fillLayerId]) {
            if (map.getLayer(id)) map.removeLayer(id);
        }
        for (const id of [sourceId, footprintSourceId]) {
            if (map.getSource(id)) map.removeSource(id);
        }
    };

    return liveDataSync(map, {
        sectionKey: 'gdacs', initialConfig: config, mount, refresh, unmount, refreshMs: 60000,
    });
}
