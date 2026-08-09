import { liveLayerSync } from './_refresh.js';
import { standardLegend, insertBeforeExtension } from './_legend.js';
import { CMAP_MAGMA, CMAP_TURBO, CMAP_VIRIDIS, CMAP_INFERNO, CMAP_COOLWARM, rgbToRgba, twoSlopePos } from './_colormaps.js';

// Insert "_<species>_<mode>" before the extension: "data/greenhouse_gases.png" ->
// "data/greenhouse_gases_co2_absolute.png". The backend always keeps all 4
// species x mode combinations fresh on disk (CamsGhgForecastCollector/
// CamsEgg4BaselineCollector fetch unconditionally of species/mode; GhgUpdater renders
// all 4 every cycle -- see tasks/greenhouse_gases.py), so switching species or mode in
// the config UI applies on this layer's next poll tick with no render wait, same as
// sst.js's mode-only equivalent.
function speciesModeFilename(outfile, species, mode) {
    return insertBeforeExtension(outfile, `_${species || 'co2'}_${mode || 'absolute'}`);
}

// Mirrors tasks/greenhouse_gases.py's _PALETTES (absolute mode) -- baked LUTs from
// _colormaps.js so the client-drawn key matches the server-rendered image's cmap.
const PALETTES = { thermal: CMAP_MAGMA, vivid: CMAP_TURBO, deep: CMAP_VIRIDIS, ocean: CMAP_INFERNO };
const DISPLAY_UNIT = { co2: 'ppm', ch4: 'ppb' };
const SCALE_SETTING_KEYS = { co2: ['co2_min_ppm', 'co2_max_ppm'], ch4: ['ch4_min_ppb', 'ch4_max_ppb'] };
const BASELINE_YEAR_MIN = 2003, BASELINE_YEAR_MAX = 2020;

// Anomaly mode's vmin/vmax are auto-scaled from live data (98th percentile of
// |anomaly|) server-side -- no way to know them client-side, so GhgUpdater writes
// them to this sidecar each render (mirrors sst.js's sst_meta.json fetch, itself
// mirroring wind.js's wind_meta.json for the same "data-dependent scale" problem).
// Fetched once and cached -- both species re-render every cycle regardless of the
// configured one, so a stale cached value only lags by at most one render cycle.
let ghgMetaPromise = null;
function fetchGhgMeta() {
    if (!ghgMetaPromise) {
        ghgMetaPromise = fetch(`${window.MAP_UI}/data/ghg_meta.json?t=${Date.now()}`)
            .then((r) => (r.ok ? r.json() : {}))
            .catch(() => ({}));
    }
    return ghgMetaPromise;
}

function keySpecFor(cfg, ghgMeta) {
    const species = cfg.species || 'co2';
    const titleSpecies = species === 'co2' ? 'CO2' : 'CH4';
    const unit = DISPLAY_UNIT[species];

    if (cfg.mode === 'anomaly') {
        const range = ghgMeta?.[species]?.anomaly || { vmin: -1, vmax: 1 };
        const year = Math.min(BASELINE_YEAR_MAX, Math.max(BASELINE_YEAR_MIN, Number(cfg.baseline_year) || BASELINE_YEAR_MIN));
        return {
            lut: rgbToRgba(CMAP_COOLWARM),
            toPos: twoSlopePos(range.vmin, range.vmax),
            ticks: [range.vmin, range.vmin / 2, 0, range.vmax / 2, range.vmax],
            title: `${titleSpecies} Anomaly vs ${year} (${unit})`,
            tickFormat: '%.1f',
        };
    }

    const [minKey, maxKey] = SCALE_SETTING_KEYS[species];
    const vmin = Number(cfg[minKey] ?? 0);
    const vmax = Number(cfg[maxKey] ?? 1);
    const paletteKey = String(cfg[`${species}_palette`] || 'thermal').toLowerCase();
    return {
        lut: rgbToRgba(PALETTES[paletteKey] || PALETTES.thermal),
        vmin, vmax, ticks: [0, 1, 2, 3, 4].map((i) => vmin + (i / 4) * (vmax - vmin)),
        title: `${titleSpecies} Concentration (${unit})`,
        tickFormat: '%d',
    };
}

export function loadLayer(map, config) {
    const sourceId = 'greenhouse-gases-source';
    const layerId  = 'greenhouse-gases-layer';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${speciesModeFilename(cfg.outfile, cfg.species, cfg.mode)}`;
    const legend = standardLegend(
        'greenhouse-gases-legend-slot',
        (cfg) => keySpecFor(cfg, cfg._ghgMeta),
        1,
    );

    const addLegend = async (cfg) => {
        const ghgMeta = cfg.mode === 'anomaly' ? await fetchGhgMeta() : null;
        legend.addLegend({ ...cfg, _ghgMeta: ghgMeta });
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
        legend.removeLegend();
    };

    // A species/mode switch changes urlFor itself (both ARE part of the filename), so
    // that case is already covered by the default imageUrl regen chase. A
    // palette/scale-only change doesn't change the filename, but it DOES force a full
    // server-side re-render of the image -- GhgUpdater.run()'s freshness check also
    // compares a persisted settings signature, not just source-data mtime (see
    // Updater._is_render_fresh / GhgUpdater._mode_settings_signature in
    // tasks/greenhouse_gases.py).
    return liveLayerSync(map, {
        sectionKey: 'greenhouse_gases', initialConfig: config, mount, refresh, unmount,
        imageUrl: urlFor,
    });
}
