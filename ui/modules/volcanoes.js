import { liveDataSync } from './_datasync.js';

export function loadLayer(map, config) {
    const sourceId = 'volcanoes-source';
    const layerId  = 'volcanoes-layer';
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 15 });

    const urlFor = (cfg) => `${window.WM_API}/volcanoes/geojson`
        + `?vei_min=${cfg.vei_min}`
        + `&significant=${cfg.significant}`
        + `&codes=${(cfg.erupt_date_codes || []).join(',')}&t=${Date.now()}`;

    const fetchData = async (cfg) => {
        const r = await fetch(urlFor(cfg));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    };

    const onEnter = (e) => {
        if (!e.features.length) return;
        map.getCanvas().style.cursor = 'pointer';
        const p = e.features[0].properties;
        const coords = e.features[0].geometry.coordinates.slice();
        popup.setLngLat(coords).setHTML(
            `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:3px;">
                <strong style="font-size:13px;color:#333;">${p.name || 'Unknown Volcano'}</strong>
                <hr style="border:0;border-top:1px solid #ccc;margin:4px 0;">
                <div><span style="color:#666;width:45px;display:inline-block;">VEI:</span> <strong>${p.vei}</strong></div>
                <div><span style="color:#666;width:45px;display:inline-block;">Code:</span> <strong>${p.code || 'N/A'}</strong></div>
            </div>`).addTo(map);
    };
    const onLeave = () => { map.getCanvas().style.cursor = ''; popup.remove(); };

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
        map.on('mouseenter', layerId, onEnter);
        map.on('mouseleave', layerId, onLeave);
    };

    const refresh = async (cfg) => {
        const data = await fetchData(cfg);
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        map.off('mouseenter', layerId, onEnter);
        map.off('mouseleave', layerId, onLeave);
        popup.remove();
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    // Volcanoes barely change — long refresh.
    return liveDataSync(map, { sectionKey: 'volcanoes', initialConfig: config, mount, refresh, unmount, refreshMs: 600000 });
}