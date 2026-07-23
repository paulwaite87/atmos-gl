import { liveLayerSync } from './_refresh.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

// Insert "_<mode>" before the extension: "data/sst.png" -> "data/sst_anomaly.png".
// The backend always keeps BOTH modes' renders fresh on disk (SstCollector fetches
// both netCDFs unconditionally; SSTUpdater renders both every cycle -- see
// tasks/sst.py), so switching `sst.mode` in the config UI applies on this layer's next
// poll tick with no render wait, same as any other setting change.
function modeFilename(outfile, mode) {
    const i = outfile.lastIndexOf('.');
    const base = i !== -1 ? outfile.slice(0, i) : outfile;
    const ext  = i !== -1 ? outfile.slice(i)    : '';
    return `${base}_${mode || 'absolute'}${ext}`;
}

export function loadLayer(map, config) {
    const sourceId = 'sst-source';
    const layerId  = 'sst-layer';
    const slotId   = 'sst-legend-slot';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${modeFilename(cfg.outfile, cfg.mode)}`;

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(modeFilename(cfg.outfile, cfg.mode))}?t=${Date.now()}`);
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

    // Palette/mode changes never touch the raster image's own mtime the way a genuine
    // mode switch does (mode IS part of urlFor, so that case is already covered by the
    // default imageUrl regen chase) -- but a palette-only change re-renders just the
    // legend key server-side, which keyUrl's independent chase catches.
    const keyUrlFor = (cfg) => keyFilename(modeFilename(cfg.outfile, cfg.mode));

    return liveLayerSync(map, {
        sectionKey: 'sst', initialConfig: config, mount, refresh, unmount,
        imageUrl: urlFor, keyUrl: (cfg) => `${window.MAP_UI}/${keyUrlFor(cfg)}`,
    });
}
