import { liveLayerSync } from './_refresh.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

// Insert "_<species>_<mode>" before the extension: "data/greenhouse_gases.png" ->
// "data/greenhouse_gases_co2_absolute.png". The backend always keeps all 4
// species x mode combinations fresh on disk (GeosCfGhgCollector/
// CamsEgg4BaselineCollector fetch unconditionally of species/mode; GhgUpdater renders
// all 4 every cycle -- see tasks/greenhouse_gases.py), so switching species or mode in
// the config UI applies on this layer's next poll tick with no render wait, same as
// sst.js's mode-only equivalent.
function speciesModeFilename(outfile, species, mode) {
    const i = outfile.lastIndexOf('.');
    const base = i !== -1 ? outfile.slice(0, i) : outfile;
    const ext  = i !== -1 ? outfile.slice(i)    : '';
    return `${base}_${species || 'co2'}_${mode || 'absolute'}${ext}`;
}

export function loadLayer(map, config) {
    const sourceId = 'greenhouse-gases-source';
    const layerId  = 'greenhouse-gases-layer';
    const slotId   = 'greenhouse-gases-legend-slot';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${speciesModeFilename(cfg.outfile, cfg.species, cfg.mode)}`;

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(speciesModeFilename(cfg.outfile, cfg.species, cfg.mode))}?t=${Date.now()}`);
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

    // Palette/scale-only changes never touch the raster image's own mtime the way a
    // genuine species/mode switch does (both ARE part of urlFor, so that case is
    // already covered by the default imageUrl regen chase) -- but a palette-only
    // change re-renders just the legend key server-side, which keyUrl's independent
    // chase catches. Same reasoning as sst.js's keyUrlFor.
    const keyUrlFor = (cfg) => keyFilename(speciesModeFilename(cfg.outfile, cfg.species, cfg.mode));

    return liveLayerSync(map, {
        sectionKey: 'greenhouse_gases', initialConfig: config, mount, refresh, unmount,
        imageUrl: urlFor, keyUrl: (cfg) => `${window.MAP_UI}/${keyUrlFor(cfg)}`,
    });
}
