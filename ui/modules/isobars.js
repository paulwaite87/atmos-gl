import { liveLayerSync } from './_refresh.js';

export function loadLayer(map, config) {
    const sourceId = 'isobars-source';
    const layerId  = 'isobars-layer';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${cfg.outfile}`;

    const mount = (cfg) => {
        if (map.getSource(sourceId)) return;
        map.addSource(sourceId, {
            type: 'image',
            url: `${urlFor(cfg)}?t=${Date.now()}`,
            coordinates,
        });
        map.addLayer({
            id: layerId, type: 'raster', source: sourceId,
            paint: { 'raster-opacity': 0.85, 'raster-fade-duration': 0 },
        });
    };

    const refresh = (cfg) => {
        const s = map.getSource(sourceId);
        if (s) s.updateImage({ url: `${urlFor(cfg)}?t=${Date.now()}` });
    };

    const unmount = () => {
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    liveLayerSync(map, { sectionKey: 'isobars', initialConfig: config, mount, refresh, unmount });
}