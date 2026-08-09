import { createStaticFillLayer } from './_webglfill.js';
import { standardLegend, insertBeforeExtension } from './_legend.js';
import { CMAP_MAGMA, CMAP_TURBO, CMAP_VIRIDIS, CMAP_INFERNO, CMAP_COOLWARM, rgbToRgba, buildScaledLUT, twoSlopePos } from './_colormaps.js';
import { opacityUniform } from './_opacity.js';

// Insert "_<species>_<mode>" before the extension: "data/greenhouse_gases.png" ->
// "data/greenhouse_gases_co2_absolute.png". The backend always keeps all 4
// species x mode combinations fresh on disk (CamsGhgForecastCollector/
// CamsEgg4BaselineCollector fetch unconditionally of species/mode; GhgUpdater renders
// all 4 every cycle -- see tasks/greenhouse_gases.py), so switching species or mode in
// the config UI applies on this layer's next poll tick with no render wait, same as
// sst.js's mode-only equivalent. Since #312, this path holds a raw, un-colored data
// texture (encode_frames), not a colored image.
function speciesModeFilename(outfile, species, mode) {
    return insertBeforeExtension(outfile, `_${species || 'co2'}_${mode || 'absolute'}`);
}

// Mirrors tasks/greenhouse_gases.py's _ABS_ENCODE_DOMAIN/_ANOMALY_ENCODE_DOMAIN --
// baked LUTs from _colormaps.js so the client-drawn key/fill match the same source
// cmap. The fixed physical domain each species' raw data texture is encoded into
// server-side (issue #312) -- NOT the user's live min/max settings, which only remap
// WITHIN this domain client-side (see lutFor below), so a palette/scale change never
// needs a server round-trip.
const PALETTES = { thermal: CMAP_MAGMA, vivid: CMAP_TURBO, deep: CMAP_VIRIDIS, ocean: CMAP_INFERNO };
const DISPLAY_UNIT = { co2: 'ppm', ch4: 'ppb' };
const SCALE_SETTING_KEYS = { co2: ['co2_min_ppm', 'co2_max_ppm'], ch4: ['ch4_min_ppb', 'ch4_max_ppb'] };
const ABS_ENCODE_DOMAIN = { co2: [380.0, 450.0], ch4: [1600.0, 2100.0] };
const ANOMALY_ENCODE_DOMAIN = { co2: [-100.0, 100.0], ch4: [-300.0, 300.0] };
const BASELINE_YEAR_MIN = 2003, BASELINE_YEAR_MAX = 2020;

// Anomaly mode's vmin/vmax are auto-scaled from live data (98th percentile of
// |anomaly|) server-side -- no way to know them client-side, so GhgUpdater writes
// them to this sidecar each render (mirrors sst.js's sst_meta.json fetch, itself
// mirroring wind.js's wind_meta.json for the same "data-dependent scale" problem).
// Fetched once and cached forever -- both species re-render every cycle regardless of
// the configured one, so the legend key and the fill layer's colour LUT both read the
// same resolved value, refreshed next time either naturally re-runs (every
// liveLayerSync poll tick).
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

// The fill layer's colour LUT: same live vmin/vmax/palette the legend key uses, but
// remapped onto the FIXED per-species physical encode domain above (see
// buildScaledLUT) rather than sampled 1:1 -- the encoded texture's own domain is
// wider/constant per species, so the user's live display range only ever selects a
// WINDOW within it.
function lutFor(cfg, ghgMeta) {
    const species = cfg.species || 'co2';
    if (cfg.mode === 'anomaly') {
        const range = ghgMeta?.[species]?.anomaly || { vmin: -1, vmax: 1 };
        const [physMin, physMax] = ANOMALY_ENCODE_DOMAIN[species];
        return buildScaledLUT({
            physicalMin: physMin, physicalMax: physMax,
            toPos: twoSlopePos(range.vmin, range.vmax),
            sourceCmap: CMAP_COOLWARM,
        });
    }
    const [minKey, maxKey] = SCALE_SETTING_KEYS[species];
    const vmin = Number(cfg[minKey] ?? 0);
    const vmax = Number(cfg[maxKey] ?? 1);
    const paletteKey = String(cfg[`${species}_palette`] || 'thermal').toLowerCase();
    const [physMin, physMax] = ABS_ENCODE_DOMAIN[species];
    return buildScaledLUT({
        physicalMin: physMin, physicalMax: physMax,
        toPos: (v) => (v - vmin) / Math.max(1e-9, vmax - vmin),
        sourceCmap: PALETTES[paletteKey] || PALETTES.thermal,
    });
}

export function loadLayer(map, config) {
    let ghgMeta = null;
    fetchGhgMeta().then((m) => { ghgMeta = m; });

    const urlFor = (cfg) => `${window.MAP_UI}/${speciesModeFilename(cfg.outfile, cfg.species, cfg.mode)}`;
    const legend = standardLegend('greenhouse-gases-legend-slot', (cfg) => keySpecFor(cfg, ghgMeta), 1);

    return createStaticFillLayer(map, {
        sectionKey: 'greenhouse_gases',
        initialConfig: config,
        dataUrl: urlFor,
        physicalDomain: (cfg) => (cfg.mode === 'anomaly' ? ANOMALY_ENCODE_DOMAIN : ABS_ENCODE_DOMAIN)[cfg.species || 'co2'],
        fragmentBody: `
            uniform float u_alpha;
            vec4 shade(float value, vec2 uv) {
                // value is already back in physical units (main()'s decodeNorm()*u_span+
                // u_vmin) -- re-derive the [0,1] LUT sample position from the SAME
                // u_vmin/u_span uniforms (visible here: GLSL globals are shader-wide),
                // since u_cmap was built by buildScaledLUT() to span that exact physical
                // domain (see lutFor() in this file).
                float t = clamp((value - u_vmin) / u_span, 0.0, 1.0);
                vec4 c = texture(u_cmap, vec2(t, 0.5));
                return vec4(c.rgb, c.a * u_alpha);
            }`,
        // 0.85 fallback matches the OLD raster-image layer's hardcoded raster-opacity
        // (the greenhouse_gases.opacity setting existed in FIELD_SPECS but was never
        // actually wired to anything -- the raster source's opacity was a fixed
        // constant). Now genuinely live, like every other client-LUT layer.
        customUniforms: (cfg) => ({
            u_alpha: opacityUniform(cfg, 0.85),
        }),
        colormap: (cfg) => lutFor(cfg, ghgMeta),
        onMount: (cfg) => legend.addLegend(cfg),
        onRefresh: (cfg) => legend.addLegend(cfg),
        onUnmount: legend.removeLegend,
    });
}
