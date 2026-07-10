import { liveLayerSync } from './_refresh.js';

export function loadLayer(map, config) {
    const sourceId = 'clouds-source';
    const layerId  = 'clouds-layer';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    // Backend always writes to data/cloud_map.png (hardcoded server-side, no longer a
    // user-editable `outfile` config setting).
    const urlFor = () => `${window.MAP_UI}/data/cloud_map.png`;

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

    return liveLayerSync(map, {
        sectionKey: 'clouds', initialConfig: config,
        mount, refresh, unmount, imageUrl: urlFor,
    });
}