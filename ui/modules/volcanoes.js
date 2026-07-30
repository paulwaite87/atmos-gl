import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, popupCard, preloadIcons } from './_feedhelpers.js';

export function loadLayer(map, config) {
    const sourceId = 'volcanoes-source';
    const layerId  = 'volcanoes-layer';
    const volcanoIcons = [
        { id: 'volcano-new', url: '/images/volcano_new.png' },
        { id: 'volcano-continuing', url: '/images/volcano_continuing.png' },
    ];
    let stopPopup = null;

    const urlFor = () => `${window.WM_API}/volcanoes/geojson?t=${Date.now()}`;

    const fetchData = () => fetchOrThrow(urlFor());

    const popupHtml = (f) => {
        const p = f.properties;
        const rows = [{ label: 'Country', value: p.country || 'N/A' }];
        if (p.activity_type) rows.push({ label: 'Activity', value: p.activity_type });
        if (p.report_description) rows.push({ label: 'Report', value: p.report_description });
        if (p.hans_alert_level || p.hans_color_code) {
            rows.push({
                label: 'USGS Alert',
                value: `${p.hans_alert_level || 'N/A'} (${p.hans_color_code || 'N/A'})`,
            });
        }
        return popupCard({
            title: p.name || 'Unknown Volcano',
            padding: 3,
            rows,
        });
    };

    const mount = async (cfg) => {
        await preloadIcons(map, volcanoIcons);
        const data = await fetchData();
        if (map.getSource(sourceId)) return;
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId,
            layout: {
                'icon-image': ['case', ['get', 'is_new'], 'volcano-new', 'volcano-continuing'],
                'icon-size': 0.6 * (cfg.icon_zoom ?? 1.0),
                'icon-allow-overlap': true, 'icon-ignore-placement': true,
            },
        });
        stopPopup = hoverPopup(map, layerId, { html: popupHtml });
    };

    const refresh = async () => {
        const data = await fetchData();
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        stopPopup?.();
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    // GVP's report is weekly; HANS enrichment is the only thing that could change
    // faster, so a long refresh (matching the old static layer's cadence) is still fine.
    return liveDataSync(map, { sectionKey: 'volcanoes', initialConfig: config, mount, refresh, unmount, refreshMs: 600000 });
}
