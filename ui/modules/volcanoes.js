import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';

export function loadLayer(map, config) {
    const sourceId = 'volcanoes-source';
    const layerId  = 'volcanoes-layer';
    let stopPopup = null;

    const urlFor = (cfg) => {
        // Build the query so UNSET config values are omitted or sent as proper defaults,
        // never the literal string "undefined" (which fails the API's bool/int coercion
        // with a 422). vei_min -> int (default 0); significant -> real bool (default
        // false); codes is always present (the API requires it; empty string is fine).
        const params = new URLSearchParams();
        params.set('vei_min', String(Number(cfg.vei_min) || 0));
        params.set('significant', cfg.significant ? 'true' : 'false');
        params.set('codes', (cfg.erupt_date_codes || []).join(','));
        params.set('t', String(Date.now()));
        return `${window.WM_API}/volcanoes/geojson?${params.toString()}`;
    };

    const fetchData = async (cfg) => {
        const r = await fetch(urlFor(cfg));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    };

    const popupHtml = (f) => {
        const p = f.properties;
        return `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:3px;">
                <strong style="font-size:13px;color:#333;">${p.name || 'Unknown Volcano'}</strong>
                <hr style="border:0;border-top:1px solid #ccc;margin:4px 0;">
                <div><span style="color:#666;width:45px;display:inline-block;">VEI:</span> <strong>${p.vei}</strong></div>
                <div><span style="color:#666;width:45px;display:inline-block;">Code:</span> <strong>${p.code || 'N/A'}</strong></div>
            </div>`;
    };

    const mount = async (cfg) => {
        if (!map.hasImage('volcano-icon')) {
            const img = await fetch('/images/volcano_symbol.png').then(r => r.blob()).then(createImageBitmap);
            if (!map.hasImage('volcano-icon')) map.addImage('volcano-icon', img);
        }
        const data = await fetchData(cfg);
        if (map.getSource(sourceId)) return;
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId,
            layout: {
                'icon-image': 'volcano-icon', 'icon-size': 0.6,
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

    // Volcanoes barely change — long refresh.
    return liveDataSync(map, { sectionKey: 'volcanoes', initialConfig: config, mount, refresh, unmount, refreshMs: 600000 });
}