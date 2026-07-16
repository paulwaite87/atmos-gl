import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow } from './_feedhelpers.js';

export function loadLayer(map, config) {
    const sourceId = 'fires-source';
    const layerId  = 'fires-layer';
    let stopPopup = null;

    const urlFor = (cfg) => `${window.WM_API}/fires/geojson`
        + `?min_confidence=${cfg.min_confidence ?? 'nominal'}`
        + `&expiry_hours=${cfg.expiry_hours ?? 24}&t=${Date.now()}`;

    const fetchData = (cfg) => fetchOrThrow(urlFor(cfg));

    const popupHtml = (f) => {
        const d = f.properties;
        const mins = Math.floor(d.age_minutes);
        const age = mins < 60 ? `${mins} mins ago` : `${Math.floor(mins/60)} hours ago`;
        return `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:5px;">
               <strong style="color:#ff5a1f;">Active Fire</strong> — ${d.confidence} confidence
               <hr style="margin:6px 0;"><div>FRP: <strong>${Number(d.frp).toFixed(1)} MW</strong></div>
               <div>Brightness: <strong>${Number(d.brightness).toFixed(0)} K</strong></div>
               <div>Satellite: <strong>${d.satellite}</strong></div>
               <div>Detected: <strong>${age}</strong></div></div>`;
    };

    // No image assets -- fires render as a native circle layer (radius by FRP, color by
    // confidence tier) rather than an icon symbol layer like quakes/volcanoes.
    const mount = async (cfg) => {
        const data = await fetchData(cfg);
        if (map.getSource(sourceId)) return;          // guard against races
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'circle', source: sourceId,
            paint: {
                'circle-radius': ['interpolate', ['linear'], ['get', 'frp'], 0, 3, 50, 6, 500, 12],
                'circle-color': [
                    'match', ['get', 'confidence'],
                    'high', '#ff2b00',
                    'nominal', '#ff8c00',
                    'low', '#ffd166',
                    '#ff8c00',
                ],
                'circle-opacity': 0.75,
                'circle-stroke-width': 0.5,
                'circle-stroke-color': '#7a1a00',
            },
        });
        stopPopup = hoverPopup(map, layerId, { html: popupHtml });
    };

    const refresh = async (cfg) => {
        const data = await fetchData(cfg);
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        stopPopup?.();
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, { sectionKey: 'fires', initialConfig: config, mount, refresh, unmount, refreshMs: 60000 });
}
