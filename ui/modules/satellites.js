import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { startPulse } from './_pulse.js';

export function loadLayer(map, config) {
    const sourceId = 'satellites-source';
    const layerIds = ['sat-track-past', 'sat-track-future', 'sat-position', 'sat-labels'];
    let stopPopup = null;
    let stopPulse = null;

    const urlFor = () => `${window.WM_API}/satellites/geojson?t=${Date.now()}`;
    const fetchData = async () => {
        const r = await fetch(urlFor());
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    };

    const popupHtml = (f) => {
        const p = f.properties;
        return `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:4px;">
                <strong style="color:#222;font-size:14px;">${p.name}</strong>
                <hr style="border:0;border-top:1px solid #ccc;margin:4px 0;">
                <div><span style="color:#666;width:50px;display:inline-block;">NORAD:</span> <strong>${p.norad_id}</strong></div>
                <div><span style="color:#666;width:50px;display:inline-block;">Alt:</span> <strong>${p.alt_km} km</strong></div>
            </div>`;
    };

    const mount = async () => {
        const data = await fetchData();
        if (map.getSource(sourceId)) return;
        map.addSource(sourceId, { type: 'geojson', data });

        map.addLayer({ id: 'sat-track-past', type: 'line', source: sourceId,
            filter: ['==', 'feature_type', 'TRACK_PAST'],
            paint: { 'line-color': ['get', 'color'], 'line-width': 2 } });
        map.addLayer({ id: 'sat-track-future', type: 'line', source: sourceId,
            filter: ['==', 'feature_type', 'TRACK_FUTURE'],
            paint: { 'line-color': ['get', 'color'], 'line-width': 2, 'line-dasharray': [2, 2] } });
        map.addLayer({ id: 'sat-position', type: 'circle', source: sourceId,
            filter: ['==', 'feature_type', 'POSITION'],
            paint: { 'circle-radius': 6, 'circle-color': '#111111',
                     'circle-stroke-color': ['get', 'color'], 'circle-stroke-width': 2 } });
        map.addLayer({ id: 'sat-labels', type: 'symbol', source: sourceId,
            filter: ['==', 'feature_type', 'POSITION'],
            layout: { 'text-field': ['get', 'name'], 'text-size': 11,
                      'text-offset': [0, 1.2], 'text-anchor': 'top',
                      'text-allow-overlap': false },
            paint: { 'text-color': ['get', 'color'], 'text-halo-color': '#000', 'text-halo-width': 1 } });

        stopPopup = hoverPopup(map, 'sat-position', { offset: 10, html: popupHtml });
        stopPulse = startPulse(map, 'sat-position', 'circle-radius', { base: 5 });
    };

    const refresh = async () => {
        const data = await fetchData();
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        stopPulse?.();
        stopPopup?.();
        for (const id of layerIds) if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    // Current dot moves fast; trail/prediction barely change. ~15s keeps the dot lively
    // (the RAF pulse covers between-refresh smoothness).
    return liveDataSync(map, { sectionKey: 'satellites', initialConfig: config, mount, refresh, unmount, refreshMs: 15000 });
}