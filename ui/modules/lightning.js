import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, preloadIcons, buildPopupHtml } from './_feedhelpers.js';

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

    const fetchData = (cfg) => fetchOrThrow(urlFor(cfg));

    const popupHtml = (f) => {
        const recentMins = config.strike_recent_minutes ?? 15;
        const keepMins   = config.strike_keep_minutes ?? 60;
        const p = f.properties;
        const mins = Math.floor(p.age_minutes);
        const age = mins < 60 ? `${mins} mins ago` : `${(mins / 60).toFixed(1)} hours ago`;
        const color = mins <= recentMins ? '#28a745' : (mins <= keepMins ? '#f0ad4e' : '#d9534f');
        return buildPopupHtml({
            title: { text: `Strike at ${p.timestamp}`, variant: 'alert' },
            blocks: [
                { type: 'divider' },
                { type: 'rows', rows: [{ label: 'Age', value: age, valueColor: color }] },
            ],
        });
    };

    const mount = async (cfg) => {
        const recentMins = cfg.strike_recent_minutes ?? 15;
        const keepMins   = cfg.strike_keep_minutes ?? 60;
        await preloadIcons(map, boltIcons);
        const data = await fetchData(cfg);
        if (map.getSource(sourceId)) return;
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId,
            layout: {
                'icon-image': ['step', ['get', 'age_minutes'],
                    'bolt-white', recentMins, 'bolt-yellow', keepMins, 'bolt-red'],
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

    return liveDataSync(map, { sectionKey: 'lightning', initialConfig: config, mount, refresh, unmount, refreshMs: 60000 });
}