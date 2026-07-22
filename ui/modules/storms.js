import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { startPulse } from './_pulse.js';
import { fetchOrThrow, popupCard } from './_feedhelpers.js';

// ATCF's own storm-type/category codes (field TY in b-deck/a-deck lines) -- see
// https://www.nrlmry.navy.mil/atcf_web/docs/database/new/abdeck.txt. Translated to
// friendly English only here, at render time; the backend stores the raw code
// (StormTrack.category) unchanged so it stays reusable for anything else that might
// want it later (e.g. colour-coding by category).
const CATEGORY_LABELS = {
    DB: 'Disturbance',
    TD: 'Tropical depression',
    TS: 'Tropical storm',
    TY: 'Typhoon',
    ST: 'Super typhoon',
    TC: 'Tropical cyclone',
    HU: 'Hurricane',
    SD: 'Subtropical depression',
    SS: 'Subtropical storm',
    EX: 'Extratropical cyclone',
    PT: 'Post-tropical cyclone',
    IN: 'Inland',
    DS: 'Dissipating',
    LO: 'Low-pressure area',
    WV: 'Tropical wave',
    ET: 'Extrapolated position',
    MD: 'Monsoon depression',
    XX: 'Unspecified',
};
const KNOTS_TO_KPH = 1.852;

export function loadLayer(map, config) {
    const sourceId = 'storms-source';
    const layerIds = [
        'storms-cone', 'storms-cone-shadow', 'storms-cone-outline',
        'storms-track-past', 'storms-track-forecast', 'storms-points',
    ];
    let stopPopup = null;
    let stopPulse = null;
    let currentCfg = config;

    const urlFor = () => `${window.WM_API}/storms/geojson?t=${Date.now()}`;

    const fetchData = () => fetchOrThrow(urlFor());

    const popupHtml = (f) => {
        const p = f.properties;
        const dateStr = new Date(p.dt).toLocaleString(undefined,
            { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        const rows = [
            { label: 'Type', value: p.record_type },
        ];
        if (p.category) {
            rows.push({ label: 'Storm category', value: CATEGORY_LABELS[p.category] || p.category });
        }
        if (p.wind_kt != null) {
            rows.push({ label: 'Max wind speed', value: `${Math.round(p.wind_kt * KNOTS_TO_KPH)} kph` });
        }
        if (p.pressure_hpa != null) {
            rows.push({ label: 'Min sea-level pressure', value: `${p.pressure_hpa} hPa` });
        }
        rows.push({ label: 'Time', value: dateStr });
        if (p.tau > 0) rows.push({ label: 'Hour', value: `+${p.tau}` });
        const fontSize = Number(currentCfg.popup_fontsize) || 12;
        return popupCard({ title: p.name || p.sid, titleColor: '#ff4a4a', titleSize: 14, rows, fontSize });
    };

    const mount = async (cfg) => {
        currentCfg = cfg;
        const data = await fetchData();
        if (map.getSource(sourceId)) return;
        map.addSource(sourceId, { type: 'geojson', data });

        map.addLayer({ id: 'storms-cone', type: 'fill', source: sourceId,
            filter: ['==', 'feature_type', 'CONE'],
            paint: { 'fill-color': '#ff4a4a', 'fill-opacity': 0.2, 'fill-outline-color': '#ff4a4a' } });
        map.addLayer({ id: 'storms-cone-shadow', type: 'line', source: sourceId,
            filter: ['==', 'feature_type', 'CONE'],
            paint: { 'line-color': '#000000', 'line-width': 3, 'line-opacity': 0.3, 'line-offset': 1 } },
            'storms-cone');
        map.addLayer({ id: 'storms-cone-outline', type: 'line', source: sourceId,
            filter: ['==', 'feature_type', 'CONE'],
            paint: { 'line-color': '#ff4a4a', 'line-width': 2, 'line-opacity': 0.6 } });
        map.addLayer({ id: 'storms-track-past', type: 'line', source: sourceId,
            filter: ['==', 'feature_type', 'TRACK_PAST'],
            paint: { 'line-color': '#ff4a4a', 'line-width': 2 } });
        map.addLayer({ id: 'storms-track-forecast', type: 'line', source: sourceId,
            filter: ['==', 'feature_type', 'TRACK_FORECAST'],
            paint: { 'line-color': '#ff4a4a', 'line-width': 2, 'line-dasharray': [2, 2] } });
        map.addLayer({ id: 'storms-points', type: 'circle', source: sourceId,
            filter: ['==', 'feature_type', 'POINT'],
            paint: {
                'circle-radius': ['match', ['get', 'record_type'], 'CURRENT', 6, 4],
                'circle-color': '#111111', 'circle-stroke-color': '#ff4a4a', 'circle-stroke-width': 2,
            } });

        // 360px (240px MapLibre default * 1.5) -- the fixed "label: value" rows
        // (Storm category / Max wind speed / Min sea-level pressure) were wrapping
        // uncomfortably narrow at the default width.
        stopPopup = hoverPopup(map, 'storms-points', { offset: 10, html: popupHtml, maxWidth: '360px' });
        stopPulse = startPulse(map, 'storms-points', 'circle-radius', {
            base: 6, toValue: (r) => ['match', ['get', 'record_type'], 'CURRENT', r, 4],
        });
    };

    const refresh = async (cfg) => {
        currentCfg = cfg;
        const data = await fetchData();
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        stopPulse?.();
        stopPopup?.();
        for (const id of layerIds) if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, { sectionKey: 'storms', initialConfig: config, mount, refresh, unmount, refreshMs: 120000 });
}