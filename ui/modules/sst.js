import { liveLayerSync } from './_refresh.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

// Backend always writes to data/sst_<mode>.png (hardcoded server-side, no longer a
// user-editable `outfile` config setting -- see tasks/sst.py). Both modes' renders are
// always kept fresh (SstCollector fetches both netCDFs unconditionally; SSTUpdater
// renders both every cycle), so switching `sst.mode` in the config UI applies on this
// layer's next poll tick with no render wait, same as any other setting change.
function modeFilename(mode) {
    return `data/sst_${mode || 'absolute'}.png`;
}

export function loadLayer(map, config) {
    const sourceId = 'sst-source';
    const layerId  = 'sst-layer';
    const slotId   = 'sst-legend-slot';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${modeFilename(cfg.mode)}`;

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(modeFilename(cfg.mode))}?t=${Date.now()}`);
    };

    const mount = (cfg) => {
        if (!map.getSource(sourceId)) {
            map.addSource(sourceId, { type: 'image', url: `${urlFor(cfg)}?t=${Date.now()}`, coordinates });
            map.addLayer({ id: layerId, type: 'raster', source: sourceId,
                           paint: { 'raster-opacity': 0.85, 'raster-fade-duration': 0 } });
        }
        addLegend(cfg);
    };

    const refresh = (cfg) => {
        const s = map.getSource(sourceId);
        if (s) s.updateImage({ url: `${urlFor(cfg)}?t=${Date.now()}` });
        addLegend(cfg);
    };

    const unmount = () => {
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
        removeLegend(slotId);
    };

    return liveLayerSync(map, { sectionKey: 'sst', initialConfig: config, mount, refresh, unmount, imageUrl: urlFor });
}
