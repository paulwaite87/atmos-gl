import { liveLayerSync } from './_refresh.js';
import { standardLegend, insertBeforeExtension } from './_legend.js';

// Insert "_<mode>" before the extension: "data/sst.png" -> "data/sst_anomaly.png".
// The backend always keeps BOTH modes' renders fresh on disk (SstCollector fetches
// both netCDFs unconditionally; SSTUpdater renders both every cycle -- see
// tasks/sst.py), so switching `sst.mode` in the config UI applies on this layer's next
// poll tick with no render wait, same as any other setting change.
function modeFilename(outfile, mode) {
    return insertBeforeExtension(outfile, `_${mode || 'absolute'}`);
}

export function loadLayer(map, config) {
    const sourceId = 'sst-source';
    const layerId  = 'sst-layer';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${modeFilename(cfg.outfile, cfg.mode)}`;
    const legend = standardLegend('sst-legend-slot', (cfg) => modeFilename(cfg.outfile, cfg.mode), 1);

    const mount = (cfg) => {
        if (!map.getSource(sourceId)) {
            map.addSource(sourceId, { type: 'image', url: `${urlFor(cfg)}?t=${Date.now()}`, coordinates });
            map.addLayer({ id: layerId, type: 'raster', source: sourceId,
                           paint: { 'raster-opacity': 0.85, 'raster-fade-duration': 0 } });
        }
        legend.addLegend(cfg);
    };

    const refresh = (cfg) => {
        const s = map.getSource(sourceId);
        if (s) s.updateImage({ url: `${urlFor(cfg)}?t=${Date.now()}` });
        legend.addLegend(cfg);
    };

    const unmount = () => {
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
        legend.removeLegend();
    };

    // A mode switch changes urlFor itself (mode IS part of the filename), so that case
    // is already covered by the default imageUrl regen chase. A palette/scale-only
    // change doesn't change the filename, but it DOES force a full server-side
    // re-render of both the image and its key together -- SSTUpdater.run()'s freshness
    // check also compares a persisted settings signature, not just source-data mtime
    // (see Updater._is_render_fresh / SSTUpdater._mode_settings_signature in
    // tasks/sst.py) -- so keyUrl's independent chase is what actually catches that
    // case landing.
    return liveLayerSync(map, {
        sectionKey: 'sst', initialConfig: config, mount, refresh, unmount,
        imageUrl: urlFor, keyUrl: legend.keyUrl,
    });
}
