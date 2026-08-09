import { liveLayerSync } from './_refresh.js';
import { standardLegend, insertBeforeExtension } from './_legend.js';
import { CMAP_MAGMA, CMAP_TURBO, CMAP_VIRIDIS, CMAP_INFERNO, CMAP_COOLWARM, rgbToRgba, twoSlopePos } from './_colormaps.js';

// Insert "_<mode>" before the extension: "data/sst.png" -> "data/sst_anomaly.png".
// The backend always keeps BOTH modes' renders fresh on disk (SstCollector fetches
// both netCDFs unconditionally; SSTUpdater renders both every cycle -- see
// tasks/sst.py), so switching `sst.mode` in the config UI applies on this layer's next
// poll tick with no render wait, same as any other setting change.
function modeFilename(outfile, mode) {
    return insertBeforeExtension(outfile, `_${mode || 'absolute'}`);
}

// Mirrors tasks/sst.py's plot() palette map (absolute mode) -- baked LUTs from
// _colormaps.js so the client-drawn key matches the server-rendered image's cmap.
const PALETTES = { thermal: CMAP_MAGMA, vivid: CMAP_TURBO, deep: CMAP_VIRIDIS, ocean: CMAP_INFERNO };

// Anomaly mode's vmin/vmax are auto-scaled from live data (98th percentile of
// |anomaly|) server-side -- no way to know them client-side, so SSTUpdater writes
// them (for both modes, uniformly) to this sidecar each render. Mirrors wind.js's
// wind_meta.json fetch for the identical "data-dependent scale" problem.
let sstMetaPromise = null;
function fetchSstMeta() {
    if (!sstMetaPromise) {
        sstMetaPromise = fetch(`${window.MAP_UI}/data/sst_meta.json?t=${Date.now()}`)
            .then((r) => (r.ok ? r.json() : {}))
            .catch(() => ({}));
    }
    return sstMetaPromise;
}

function keySpecFor(cfg, sstMeta) {
    if (cfg.mode === 'anomaly') {
        const range = sstMeta?.anomaly || { vmin: -4, vmax: 4 };
        return {
            lut: rgbToRgba(CMAP_COOLWARM),
            toPos: twoSlopePos(range.vmin, range.vmax),
            ticks: [range.vmin, range.vmin / 2, 0, range.vmax / 2, range.vmax],
            title: 'SST Climatological Anomaly (°C)',
            tickFormat: '%.1f',
        };
    }
    const vmin = Number(cfg.min_c ?? 0);
    const vmax = Number(cfg.max_c ?? 32);
    const paletteKey = String(cfg.palette || 'thermal').toLowerCase();
    return {
        lut: rgbToRgba(PALETTES[paletteKey] || PALETTES.thermal),
        vmin, vmax, ticks: [0, 1, 2, 3, 4].map((i) => vmin + (i / 4) * (vmax - vmin)),
        title: 'Sea Surface Temp (°C)',
        tickFormat: '%d',
    };
}

export function loadLayer(map, config) {
    const sourceId = 'sst-source';
    const layerId  = 'sst-layer';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${modeFilename(cfg.outfile, cfg.mode)}`;
    const legend = standardLegend('sst-legend-slot', (cfg) => keySpecFor(cfg, cfg._sstMeta), 1);

    const addLegend = async (cfg) => {
        const sstMeta = cfg.mode === 'anomaly' ? await fetchSstMeta() : null;
        legend.addLegend({ ...cfg, _sstMeta: sstMeta });
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

    // A mode switch changes urlFor itself (mode IS part of the filename), so that case
    // is already covered by the default imageUrl regen chase. A palette/scale-only
    // change doesn't change the filename, but it DOES force a full server-side
    // re-render of the image -- SSTUpdater.run()'s freshness check also compares a
    // persisted settings signature, not just source-data mtime (see
    // Updater._is_render_fresh / SSTUpdater._mode_settings_signature in tasks/sst.py).
    return liveLayerSync(map, {
        sectionKey: 'sst', initialConfig: config, mount, refresh, unmount,
        imageUrl: urlFor,
    });
}
