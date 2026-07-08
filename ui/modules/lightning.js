import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';

export function loadLayer(map, config) {
    const sourceId = 'lightning-source';
    const layerId  = 'lightning-layer';
    let stopPopup = null;
    const boltIcons = [
        { id: 'bolt-white',  url: '/images/bolt_white.png' },
        { id: 'bolt-yellow', url: '/images/bolt_yellow.png' },
        { id: 'bolt-red',    url: '/images/bolt_red.png' },
    ];

    const urlFor = (cfg) => `${window.WM_API}/lightning/geojson`
        + `?expiry_hours=${cfg.strike_expiry_hours ?? 2}&t=${Date.now()}`;

    const fetchData = async (cfg) => {
        const r = await fetch(urlFor(cfg));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    };

    const popupHtml = (f) => {
        const recentMins = config.strike_recent_minutes ?? 15;
        const keepMins   = config.strike_keep_minutes ?? 60;
        const p = f.properties;
        const mins = Math.floor(p.age_minutes);
        const age = mins < 60 ? `${mins} mins ago` : `${(mins / 60).toFixed(1)} hours ago`;
        const color = mins <= recentMins ? '#28a745' : (mins <= keepMins ? '#f0ad4e' : '#d9534f');
        return `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:5px;">
                <strong style="color:#ff4a4a;font-size:14px;">Strike at ${p.timestamp}</strong>
                <hr style="border:0;border-top:1px solid #ccc;margin:6px 0;">
                <div><span style="color:#666;width:40px;display:inline-block;">Age:</span> <strong style="color:${color};">${age}</strong></div>
            </div>`;
    };

    const mount = async (cfg) => {
        const recentMins = cfg.strike_recent_minutes ?? 15;
        const keepMins   = cfg.strike_keep_minutes ?? 60;
        await Promise.all(boltIcons.map(async (ic) => {
            if (map.hasImage(ic.id)) return;
            const res = await fetch(`${window.location.origin}${ic.url}`);
            if (!res.ok) throw new Error(`Could not load ${ic.id}`);
            map.addImage(ic.id, await createImageBitmap(await res.blob()));
        }));
        const data = await fetchData(cfg);
        if (map.getSource(sourceId)) return;
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId,
            layout: {
                'icon-image': ['step', ['get', 'age_minutes'],
                    'bolt-white', recentMins, 'bolt-yellow', keepMins, 'bolt-red'],
                'icon-size': 0.8,
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

    return liveDataSync(map, { sectionKey: 'lightning', initialConfig: config, mount, refresh, unmount, refreshMs: 60000 });
}