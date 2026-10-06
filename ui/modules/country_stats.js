import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, buildPopupHtml, escapeHtml } from './_feedhelpers.js';
import { standardLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';
import { CMAP_VIRIDIS, CMAP_MAGMA, CMAP_INFERNO, CMAP_YLORRD, CMAP_COOLWARM, CMAP_TURBO } from './_colormaps.js';

// Country Statistics (collectors/country_stats.py, lib/country_stats.py): one Our World
// in Data indicator at a time, each country coloured by its own most recent value.
// Countries are Natural Earth 1:50m polygons bundled as /geo/countries_50m.json
// (tools/build_country_geometry.py), each carrying the `code` the route's values are
// keyed by; the colours are one 'match' expression over those codes, rebuilt whenever
// the indicator or its data changes -- no re-render on the server.
const COLORMAPS = {
    viridis: CMAP_VIRIDIS, magma: CMAP_MAGMA, inferno: CMAP_INFERNO,
    ylorrd: CMAP_YLORRD, coolwarm: CMAP_COOLWARM, turbo: CMAP_TURBO,
};
// No value, or only a stale one: a flat, see-through grey, so "no data" reads
// differently from "layer off".
export const NO_DATA_COLOR = 'rgba(150,150,150,0.35)';
const OUTLINE_COLOR = 'rgba(255,255,255,0.35)';
const PERCENTILE_LO = 0.02;
const PERCENTILE_HI = 0.98;
const OWID_LINK = '<a href="https://ourworldindata.org" target="_blank" rel="noopener noreferrer">Our World in Data</a>';

const isLog = (indicator) => indicator.scale === 'log';

// A value in the space the colour ramp is linear in; null where it can't be shown
// (zero or negative on a log scale).
export function toScale(value, indicator) {
    if (!isLog(indicator)) return value;
    return value > 0 ? Math.log10(value) : null;
}

function quantile(sorted, q) {
    const i = (sorted.length - 1) * q;
    const lo = Math.floor(i);
    const hi = Math.ceil(i);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}

// [lo, hi] of the colour ramp, in scale space: the catalog's fixed domain if it has
// one, else the 2nd-98th percentile of the values shown -- so one or two outliers
// (China and the US for oil) don't flatten everyone else into one colour.
export function colorDomain(countries, indicator) {
    if (indicator.domain) return indicator.domain.map((v) => toScale(v, indicator));
    const scaled = Object.values(countries)
        .filter((c) => !c.stale)
        .map((c) => toScale(c.value, indicator))
        .filter((v) => v !== null)
        .sort((a, b) => a - b);
    if (!scaled.length) return [0, 1];
    const lo = quantile(scaled, PERCENTILE_LO);
    const hi = quantile(scaled, PERCENTILE_HI);
    return hi > lo ? [lo, hi] : [lo - 0.5, lo + 0.5];
}

// 0..1 along the ramp, values beyond the domain clipped to its ends; null if the value
// can't be placed.
export function rampPosition(value, indicator, [lo, hi]) {
    const v = toScale(value, indicator);
    if (v === null) return null;
    return Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
}

const lutOf = (indicator) => COLORMAPS[indicator.colormap] || CMAP_VIRIDIS;

function rgbAt(lut, t) {
    const o = Math.round(t * 255) * 3;
    return `rgb(${lut[o]},${lut[o + 1]},${lut[o + 2]})`;
}

// The fill-color expression: each country with a current value -> its ramp colour,
// every other polygon (stale, no data, no OWID country at all) -> NO_DATA_COLOR.
export function fillColorExpression(data) {
    const { indicator, countries } = data;
    const domain = colorDomain(countries, indicator);
    const lut = lutOf(indicator);
    const pairs = [];
    for (const [code, c] of Object.entries(countries)) {
        if (c.stale) continue;
        const t = rampPosition(c.value, indicator, domain);
        if (t !== null) pairs.push(code, rgbAt(lut, t));
    }
    // 'match' needs at least one label/output pair.
    return pairs.length ? ['match', ['get', 'code'], ...pairs, NO_DATA_COLOR] : NO_DATA_COLOR;
}

export function formatValue(value) {
    const abs = Math.abs(value);
    if (abs >= 1e6) {
        return value.toLocaleString('en', { notation: 'compact', maximumSignificantDigits: 3 });
    }
    if (abs >= 100) return Math.round(value).toLocaleString('en');
    return Number(value.toPrecision(3)).toLocaleString('en');
}

const unitOf = (indicator) => indicator.short_unit || indicator.unit || '';

// "$" and other symbol-like short units read better before the number.
export function withUnit(value, indicator) {
    const unit = unitOf(indicator);
    const text = formatValue(value);
    if (!unit) return text;
    if (unit === '$' || unit === '£' || unit === '€') return `${unit}${text}`;
    if (unit === '%') return `${text}%`;
    return `${text} ${unit}`;
}

// The popup's one data line for a country.
export function countryLine(entry, indicator) {
    if (!entry) return 'No data';
    if (entry.stale) return `No recent data (latest ${entry.year})`;
    return `${indicator.title}: ${withUnit(entry.value, indicator)} (${entry.year})`;
}

export function attributionHtml(indicator) {
    const citation = indicator.citation ? `${escapeHtml(indicator.citation)}, via ` : 'Via ';
    return `${citation}${OWID_LINK}`;
}

// Legend ticks in data units: the domain's ends plus, on a log scale, each power of
// ten between them that isn't crowding an end; on a linear scale, the midpoint.
export function legendTicks(indicator, [lo, hi]) {
    const fromScale = (v) => (isLog(indicator) ? 10 ** v : v);
    const ticks = [lo];
    if (isLog(indicator)) {
        for (let p = Math.ceil(lo); p <= Math.floor(hi); p++) {
            if (p - lo > 0.25 && hi - p > 0.25) ticks.push(p);
        }
    } else {
        ticks.push((lo + hi) / 2);
    }
    ticks.push(hi);
    return ticks.map(fromScale);
}

export function legendTitle(indicator) {
    const unit = indicator.unit || indicator.short_unit;
    return unit ? `${indicator.title} (${unit})` : indicator.title;
}

export function loadLayer(map, config) {
    const sourceId = 'country-stats-source';
    const fillId = 'country-stats-fill';
    const lineId = 'country-stats-line';
    let stopPopup = null;
    let geometry = null;      // fetched once; the polygons never change
    let data = null;          // the route's latest response

    const fetchData = (cfg) => {
        const params = new URLSearchParams({ indicator: cfg.indicator || 'population', t: Date.now() });
        if (cfg.max_age_years !== undefined && cfg.max_age_years !== null) {
            params.set('max_age_years', cfg.max_age_years);
        }
        return fetchOrThrow(`${window.WM_API}/country_stats?${params}`);
    };

    const legend = standardLegend('country-stats-legend-slot', () => {
        const { indicator, countries } = data;
        const domain = colorDomain(countries, indicator);
        return {
            lut: lutOf(indicator),
            stride: 3,
            toPos: (v) => rampPosition(v, indicator, domain) ?? 0,
            ticks: legendTicks(indicator, domain),
            title: legendTitle(indicator),
            tickFormat: formatValue,
        };
    }, 0.7);

    // The citation isn't part of the canvas key, so it's appended as a line of text
    // under it, inside the same slot.
    const showLegend = (cfg) => {
        legend.addLegend(cfg);
        const slot = document.getElementById('country-stats-legend-slot');
        if (!slot || !data) return;
        const note = document.createElement('div');
        note.className = 'legend-attribution';
        note.style.cssText = 'font-size:10px;color:#ddd;max-width:200px;line-height:1.25;margin-top:2px;';
        note.innerHTML = attributionHtml(data.indicator);
        slot.appendChild(note);
    };

    const opacity = (cfg) => opacityUniform(cfg, 0.7);

    const popupHtml = (f) => {
        const { code, name } = f.properties;
        const entry = data?.countries?.[code];
        return buildPopupHtml({
            title: { text: name },
            blocks: [
                { type: 'text', text: countryLine(entry, data.indicator) },
                { type: 'notice', raw: true, color: '#6c757d', text: attributionHtml(data.indicator) },
            ],
        });
    };

    const mount = async (cfg) => {
        [geometry, data] = await Promise.all([
            geometry ? Promise.resolve(geometry) : fetchOrThrow(`${window.MAP_UI}/geo/countries_50m.json`),
            fetchData(cfg),
        ]);
        if (map.getSource(sourceId)) return;          // guard against races
        map.addSource(sourceId, { type: 'geojson', data: geometry });
        map.addLayer({
            id: fillId, type: 'fill', source: sourceId,
            paint: { 'fill-color': fillColorExpression(data), 'fill-opacity': opacity(cfg) },
        });
        map.addLayer({
            id: lineId, type: 'line', source: sourceId,
            paint: { 'line-color': OUTLINE_COLOR, 'line-width': 0.5 },
        });
        // 'move': neighbouring countries share one fill layer, so mouseenter alone
        // wouldn't update crossing a border.
        stopPopup = hoverPopup(map, fillId, { html: popupHtml, maxWidth: '300px', event: 'move' });
        showLegend(cfg);
    };

    const refresh = async (cfg) => {
        data = await fetchData(cfg);
        if (map.getLayer(fillId)) {
            map.setPaintProperty(fillId, 'fill-color', fillColorExpression(data));
            map.setPaintProperty(fillId, 'fill-opacity', opacity(cfg));
        }
        showLegend(cfg);
    };

    const unmount = () => {
        stopPopup?.();
        legend.removeLegend();
        for (const id of [lineId, fillId]) {
            if (map.getLayer(id)) map.removeLayer(id);
        }
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, {
        sectionKey: 'country_stats', initialConfig: config, mount, refresh, unmount, refreshMs: 60 * 60000,
    });
}
