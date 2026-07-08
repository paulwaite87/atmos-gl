import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { startPulse } from './_pulse.js';

export function loadLayer(map, config) {
    const sourceId = 'storms-source';
    const layerIds = [
        'storms-cone', 'storms-cone-shadow', 'storms-cone-outline',
        'storms-track-past', 'storms-track-forecast', 'storms-points',
    ];
    let stopPopup = null;
    let stopPulse = null;

    const urlFor = () => `${window.WM_API}/storms/geojson?t=${Date.now()}`;

    const fetchData = async () => {
        const r = await fetch(urlFor());
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    };

    const popupHtml = (f) => {
        const p = f.properties;
        const dateStr = new Date(p.dt).toLocaleString(undefined,
            { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        return `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:4px;">
                <strong style="color:#ff4a4a;font-size:14px;">${p.name || p.sid}</strong>
                <hr style="border:0;border-top:1px solid #ccc;margin:4px 0;">
                <div><span style="color:#666;width:45px;display:inline-block;">Type:</span> <strong>${p.record_type}</strong></div>
                <div><span style="color:#666;width:45px;display:inline-block;">Time:</span> <strong>${dateStr}</strong></div>
                ${p.tau > 0 ? `<div><span style="color:#666;width:45px;display:inline-block;">Hour:</span> <strong>+${p.tau}</strong></div>` : ''}
            </div>`;
    };

    const mount = async () => {
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

        stopPopup = hoverPopup(map, 'storms-points', { offset: 10, html: popupHtml });
        stopPulse = startPulse(map, 'storms-points', 'circle-radius', {
            base: 6, toValue: (r) => ['match', ['get', 'record_type'], 'CURRENT', r, 4],
        });
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

    return liveDataSync(map, { sectionKey: 'storms', initialConfig: config, mount, refresh, unmount, refreshMs: 120000 });
}