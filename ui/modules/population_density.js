import { createStaticFillLayer } from './_webglfill.js';
import { standardLegend } from './_legend.js';
import { CMAP_MAGMA, CMAP_TURBO, CMAP_VIRIDIS, CMAP_INFERNO, rgbToRgba, buildScaledLUT } from './_colormaps.js';
import { opacityUniform } from './_opacity.js';

// GHSL population density (tasks/population_density.py). The texture holds
// log10(people/km²) over a FIXED domain, mirroring that module's ENCODE_DOMAIN; the
// threshold, colour range and palette all apply here, so changing them never waits
// on a server render.
const ENCODE_DOMAIN = [-1, 5];
const PALETTES = { thermal: CMAP_MAGMA, vivid: CMAP_TURBO, deep: CMAP_VIRIDIS, ocean: CMAP_INFERNO };
const DEFAULT_MIN = 10;
const DEFAULT_MAX = 5000;

// [log10 min, log10 max] of the colour range. min_density is also the threshold:
// cells below it aren't drawn at all. A max at or below the min keeps a sliver of
// range rather than dividing by zero.
export function logRange(cfg) {
    const min = Math.max(0.1, Number(cfg.min_density ?? DEFAULT_MIN) || DEFAULT_MIN);
    const max = Number(cfg.max_density ?? DEFAULT_MAX) || DEFAULT_MAX;
    const lmin = Math.log10(min);
    return [lmin, Math.max(lmin + 0.1, Math.log10(Math.max(max, 0.1)))];
}

const paletteOf = (cfg) => PALETTES[String(cfg.palette || 'thermal').toLowerCase()] || PALETTES.thermal;

// Legend ticks in people/km²: the range's ends plus every power of ten between them,
// dropping a power of ten too close to an end to label legibly.
export function densityTicks(cfg) {
    const [lmin, lmax] = logRange(cfg);
    const ticks = [10 ** lmin];
    for (let p = Math.ceil(lmin); p <= Math.floor(lmax); p++) {
        if (p - lmin > 0.25 && lmax - p > 0.25) ticks.push(10 ** p);
    }
    ticks.push(10 ** lmax);
    return ticks;
}

export function formatDensity(v) {
    const r = Math.round(v);
    if (r >= 1000) return `${Number((r / 1000).toFixed(1))}k`;
    return v < 1 ? v.toFixed(1) : String(r);
}

function keySpecFor(cfg) {
    const [lmin, lmax] = logRange(cfg);
    return {
        lut: rgbToRgba(paletteOf(cfg)),
        toPos: (v) => (Math.log10(v) - lmin) / (lmax - lmin),
        ticks: densityTicks(cfg),
        title: 'Population Density (people / km²)',
        tickFormat: formatDensity,
    };
}

function lutFor(cfg) {
    const [lmin, lmax] = logRange(cfg);
    return buildScaledLUT({
        physicalMin: ENCODE_DOMAIN[0], physicalMax: ENCODE_DOMAIN[1],
        toPos: (v) => (v - lmin) / (lmax - lmin),
        sourceCmap: paletteOf(cfg),
    });
}

export function loadLayer(map, config) {
    const legend = standardLegend('population-density-legend-slot', keySpecFor, 0.7);

    return createStaticFillLayer(map, {
        sectionKey: 'population_density',
        initialConfig: config,
        dataUrl: (cfg) => `${window.MAP_UI}/${cfg.outfile}`,
        physicalDomain: () => ENCODE_DOMAIN,
        fragmentBody: `
            uniform float u_alpha;
            uniform float u_log_min;
            vec4 shade(float value, vec2 uv) {
                if (value < u_log_min) return vec4(0.0);
                float t = clamp((value - u_vmin) / u_span, 0.0, 1.0);
                vec4 c = texture(u_cmap, vec2(t, 0.5));
                return vec4(c.rgb, c.a * u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_alpha: opacityUniform(cfg, 0.7),
            u_log_min: logRange(cfg)[0],
        }),
        colormap: lutFor,
        onMount: (cfg) => legend.addLegend(cfg),
        onRefresh: (cfg) => legend.addLegend(cfg),
        onUnmount: legend.removeLegend,
    });
}
