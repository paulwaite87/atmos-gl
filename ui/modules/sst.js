import { createStaticFillLayer } from './_webglfill.js';
import { standardLegend, insertBeforeExtension } from './_legend.js';
import { CMAP_MAGMA, CMAP_TURBO, CMAP_VIRIDIS, CMAP_INFERNO, CMAP_COOLWARM, rgbToRgba, buildScaledLUT, twoSlopePos } from './_colormaps.js';
import { opacityUniform } from './_opacity.js';

// Insert "_<mode>" before the extension: "data/sst.png" -> "data/sst_anomaly.png".
// The backend always keeps BOTH modes' renders fresh on disk (SstCollector fetches
// both netCDFs unconditionally; SSTUpdater renders both every cycle -- see
// tasks/sst.py), so switching `sst.mode` in the config UI applies on this layer's next
// poll tick with no render wait, same as any other setting change. Since #312, this
// path holds a raw, un-colored data texture (encode_frames), not a colored image.
function modeFilename(outfile, mode) {
    return insertBeforeExtension(outfile, `_${mode || 'absolute'}`);
}

// Mirrors tasks/sst.py's plot() palette map (absolute mode) -- baked LUTs from
// _colormaps.js so the client-drawn key/fill match the same source cmap.
const PALETTES = { thermal: CMAP_MAGMA, vivid: CMAP_TURBO, deep: CMAP_VIRIDIS, ocean: CMAP_INFERNO };

// Fixed physical domain the raw data texture is encoded into server-side (issue #312,
// tasks/sst.py's _ABS_ENCODE_VMIN/_ABS_ENCODE_VMAX / _ANOMALY_ENCODE_VMIN/
// _ANOMALY_ENCODE_VMAX) -- NOT the user's live min_c/max_c, which only remap WITHIN
// this domain client-side (see lutFor below), so a palette/scale change never needs a
// server round-trip.
const ABS_ENCODE_VMIN = 0.0, ABS_ENCODE_VMAX = 36.0;
const ANOMALY_ENCODE_VMIN = -10.0, ANOMALY_ENCODE_VMAX = 10.0;

// Anomaly mode's vmin/vmax are auto-scaled from live data (98th percentile of
// |anomaly|) server-side -- no way to know them client-side, so SSTUpdater writes
// them (for both modes, uniformly) to this sidecar each render. Fetched once and
// cached forever (mirrors wind.js's wind_meta.json fetch-once-at-load precedent) --
// both the legend key and the fill layer's colour LUT read the same resolved value,
// refreshed next time either naturally re-runs (every liveLayerSync poll tick).
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

// The fill layer's colour LUT: same live vmin/vmax/palette the legend key uses, but
// remapped onto the FIXED physical encode domain above (see buildScaledLUT) rather
// than sampled 1:1 -- the encoded texture's own domain is wider/constant, so the
// user's live display range only ever selects a WINDOW within it.
function lutFor(cfg, sstMeta) {
    if (cfg.mode === 'anomaly') {
        const range = sstMeta?.anomaly || { vmin: -4, vmax: 4 };
        return buildScaledLUT({
            physicalMin: ANOMALY_ENCODE_VMIN, physicalMax: ANOMALY_ENCODE_VMAX,
            toPos: twoSlopePos(range.vmin, range.vmax),
            sourceCmap: CMAP_COOLWARM,
        });
    }
    const vmin = Number(cfg.min_c ?? 0);
    const vmax = Number(cfg.max_c ?? 32);
    const paletteKey = String(cfg.palette || 'thermal').toLowerCase();
    return buildScaledLUT({
        physicalMin: ABS_ENCODE_VMIN, physicalMax: ABS_ENCODE_VMAX,
        toPos: (v) => (v - vmin) / Math.max(1e-9, vmax - vmin),
        sourceCmap: PALETTES[paletteKey] || PALETTES.thermal,
    });
}

export function loadLayer(map, config) {
    let sstMeta = null;
    fetchSstMeta().then((m) => { sstMeta = m; });

    const urlFor = (cfg) => `${window.MAP_UI}/${modeFilename(cfg.outfile, cfg.mode)}`;
    const legend = standardLegend('sst-legend-slot', (cfg) => keySpecFor(cfg, sstMeta), 1);

    return createStaticFillLayer(map, {
        sectionKey: 'sst',
        initialConfig: config,
        dataUrl: urlFor,
        physicalDomain: (cfg) => cfg.mode === 'anomaly'
            ? [ANOMALY_ENCODE_VMIN, ANOMALY_ENCODE_VMAX] : [ABS_ENCODE_VMIN, ABS_ENCODE_VMAX],
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
        // (sst.opacity existed in FIELD_SPECS but was never actually wired to
        // anything -- the raster source's opacity was a fixed constant). Now
        // genuinely live, like every other client-LUT layer.
        customUniforms: (cfg) => ({
            u_alpha: opacityUniform(cfg, 0.85),
        }),
        colormap: (cfg) => lutFor(cfg, sstMeta),
        onMount: (cfg) => legend.addLegend(cfg),
        onRefresh: (cfg) => legend.addLegend(cfg),
        onUnmount: legend.removeLegend,
    });
}
