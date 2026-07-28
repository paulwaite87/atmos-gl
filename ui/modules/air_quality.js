import { liveLayerSync } from './_refresh.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

// Insert "_<variable>" before the extension: "data/air_quality.png" ->
// "data/air_quality_pm2_5.png". The backend always keeps all 3 variables fresh on
// disk (AirQualityCollector fetches unconditionally of variable; AirQualityUpdater
// renders all 3 every cycle -- see tasks/air_quality.py), so switching variable in
// the config UI applies on this layer's next poll tick with no render wait, same as
// greenhouse_gases.js's species/mode equivalent.
function variableFilename(outfile, variable) {
    const i = outfile.lastIndexOf('.');
    const base = i !== -1 ? outfile.slice(0, i) : outfile;
    const ext  = i !== -1 ? outfile.slice(i)    : '';
    return `${base}_${variable || 'pm2_5'}${ext}`;
}

export function loadLayer(map, config) {
    const sourceId = 'air-quality-source';
    const layerId  = 'air-quality-layer';
    const slotId   = 'air-quality-legend-slot';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${variableFilename(cfg.outfile, cfg.variable)}`;

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(variableFilename(cfg.outfile, cfg.variable))}?t=${Date.now()}`);
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

    // A variable switch changes urlFor itself (it IS part of the filename), so that
    // case is already covered by the default imageUrl regen chase. A scale-only
    // change doesn't change the filename, but it DOES force a full server-side
    // re-render of both the image and its key together -- AirQualityUpdater.run()'s
    // freshness check also compares a persisted settings signature, not just
    // source-data mtime (see Updater._is_render_fresh /
    // AirQualityUpdater._variable_settings_signature in tasks/air_quality.py) -- so
    // keyUrl's independent chase is what actually catches that case landing. Same
    // reasoning as greenhouse_gases.js's keyUrlFor.
    const keyUrlFor = (cfg) => keyFilename(variableFilename(cfg.outfile, cfg.variable));

    return liveLayerSync(map, {
        sectionKey: 'air_quality', initialConfig: config, mount, refresh, unmount,
        imageUrl: urlFor, keyUrl: (cfg) => `${window.MAP_UI}/${keyUrlFor(cfg)}`,
    });
}
