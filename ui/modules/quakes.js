import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, preloadIcons } from './_feedhelpers.js';

export function loadLayer(map, config) {
    const sourceId = 'quakes-source';
    const layerId  = 'quakes-layer';
    const quakeIcons = [
        { id: 'quake-new', url: '/images/earthquake_new.png' },
        { id: 'quake-old', url: '/images/earthquake_old.png' },
    ];
    let stopPopup = null;

    const urlFor = (cfg) => `${window.WM_API}/quakes/geojson`
        + `?min_mag=${cfg.min_mag ?? 3.5}`
        + `&expiry_hours=${cfg.expiry_hours ?? 12}`
        + `&recent_hours=${cfg.recent_activity_hours ?? 3}&t=${Date.now()}`;

    const fetchData = (cfg) => fetchOrThrow(urlFor(cfg));

    const popupHtml = (f) => {
        const d = f.properties;
        const mins = Math.floor(d.age_minutes);
        const age = mins < 60 ? `${mins} mins ago` : `${Math.floor(mins/60)} hours ago`;
        return `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:5px;">
               <strong style="color:#ff4a4a;">M ${Number(d.mag).toFixed(1)}</strong> — ${d.place}
               <hr style="margin:6px 0;"><div>Depth: <strong>${d.depth} km</strong></div>
               <div>Age: <strong>${age}</strong></div></div>`;
    };

    const mount = async (cfg) => {
        await preloadIcons(map, quakeIcons);
        const data = await fetchData(cfg);
        if (map.getSource(sourceId)) return;          // guard against races
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId,
            layout: {
                'icon-image': ['case', ['get', 'is_recent'], 'quake-new', 'quake-old'],
                'icon-size': 0.8 * (cfg.icon_zoom ?? 1.0),
                'icon-allow-overlap': true, 'icon-ignore-placement': true,
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

    return liveDataSync(map, { sectionKey: 'quakes', initialConfig: config, mount, refresh, unmount, refreshMs: 60000 });
}