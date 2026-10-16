import { liveDataSync } from './_datasync.js';
import { attachChoroplethInspector } from './_choropleth_popup.js';
import { fetchOrThrow, buildPopupHtml, escapeHtml } from './_feedhelpers.js';
import { standardLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';
import { CMAP_VIRIDIS, CMAP_MAGMA, CMAP_INFERNO, CMAP_YLORRD, CMAP_COOLWARM, CMAP_TURBO } from './_colormaps.js';

// Country Statistics (collectors/country_stats.py, lib/country_stats.py): one Our World
// in Data indicator at a time, each country coloured by its value as close to now as
// its data gets. Which indicator is chosen on the map itself, with the World Data
// picker (session-only, like the basemap picker; each page load starts on
// start_indicator).
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
    // Diverging (growth, migration): symmetric about zero, so zero sits at the
    // colormap's neutral middle and shrinking/growing read as opposite colours.
    if (indicator.diverging) {
        const m = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
        return [-m, m];
    }
    return hi > lo ? [lo, hi] : [lo - 0.5, lo + 0.5];
}

// 0..1 along the ramp, values beyond the domain clipped to its ends. A zero (or
// negative) value on a log scale -- a country burning no coal -- sits at the bottom
// rather than being dropped: zero is data, not "no data".
export function rampPosition(value, indicator, [lo, hi]) {
    const v = toScale(value, indicator);
    if (v === null) return 0;
    return Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
}

// The hover badge: a plain white dot, so it doesn't compete with the colour that already
// carries the value.
export function badgeSpec() {
    return { fill: '#ffffff', glyph: '' };
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

// The key's title: the indicator, its unit, and the year of its newest figures --
// how close to "now" the map is.
export function legendTitle(indicator, newestYear) {
    const unit = indicator.unit || indicator.short_unit;
    const title = unit ? `${indicator.title} (${unit})` : indicator.title;
    return newestYear ? `${title} · ${newestYear}` : title;
}

// The World Data picker's option groups: the catalog's groups (served in the globe's
// config data as country_stats_catalog) that the show_* settings leave on.
const SHOW_SETTING = { people: 'show_people', economy: 'show_economy', environment: 'show_environment' };

export function pickerGroups(catalog, cfg) {
    return (catalog || []).filter((g) => cfg[SHOW_SETTING[g.id]] !== false && g.indicators.length);
}

// Where the picker starts: start_indicator if its group is shown; null for "none";
// otherwise the first indicator of the first shown group, so the map never starts
// blank by accident. null too when no group is shown.
export function initialIndicator(catalog, cfg) {
    const groups = pickerGroups(catalog, cfg);
    if (!groups.length) return null;
    const start = cfg.start_indicator ?? 'population';
    if (start === 'none') return null;
    const shown = groups.some((g) => g.indicators.some((i) => i.id === start));
    return shown ? start : groups[0].indicators[0].id;
}

const NONE = '';

function buildPicker(onChange) {
    const wrap = document.createElement('div');
    // The basemap picker's own panel styling, so the two read as one control strip.
    wrap.className = 'basemap-picker';
    wrap.id = 'world-data-picker';
    wrap.style.marginTop = '6px';
    wrap.style.order = '1';           // below the basemap picker in the same host
    const label = document.createElement('label');
    label.className = 'basemap-picker__label';
    label.textContent = 'World Data';
    const select = document.createElement('select');
    select.className = 'basemap-picker__select';
    select.addEventListener('change', () => onChange(select.value || null));
    wrap.appendChild(label);
    wrap.appendChild(select);
    return { wrap, select };
}

function fillPickerOptions(select, groups, selected) {
    select.replaceChildren();
    const none = document.createElement('option');
    none.value = NONE; none.textContent = 'None';
    select.appendChild(none);
    for (const g of groups) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = g.label;
        for (const i of g.indicators) {
            const opt = document.createElement('option');
            opt.value = i.id; opt.textContent = i.label;
            optgroup.appendChild(opt);
        }
        select.appendChild(optgroup);
    }
    select.value = selected || NONE;
}

export function loadLayer(map, config, fullConfig = {}) {
    const sourceId = 'country-stats-source';
    const fillId = 'country-stats-fill';
    const lineId = 'country-stats-line';
    const catalog = fullConfig.country_stats_catalog || [];
    let inspector = null;
    let geometry = null;      // fetched once; the polygons never change
    let data = null;          // the selected indicator's latest route response
    let selected = null;      // the indicator id the picker shows; null: "None"
    let userPicked = false;   // a pick on the map outlives settings changes this session
    let currentCfg = config;
    let picker = null;

    const fetchData = (cfg) => {
        const params = new URLSearchParams({ indicator: selected, t: Date.now() });
        if (cfg.max_age_years !== undefined && cfg.max_age_years !== null) {
            params.set('max_age_years', cfg.max_age_years);
        }
        return fetchOrThrow(`${window.WM_API}/country_stats?${params}`);
    };

    const legend = standardLegend('country-stats-legend-slot', () => {
        const { indicator, countries, newest_year: newestYear } = data;
        const domain = colorDomain(countries, indicator);
        return {
            lut: lutOf(indicator),
            stride: 3,
            toPos: (v) => rampPosition(v, indicator, domain),
            ticks: legendTicks(indicator, domain),
            title: legendTitle(indicator, newestYear),
            tickFormat: formatValue,
        };
    }, 0.7);

    // The citation isn't part of the canvas key, so it's appended as a line of text
    // under it, inside the same slot.
    const showLegend = (cfg) => {
        if (!data) { legend.removeLegend(); return; }
        legend.addLegend(cfg);
        const slot = document.getElementById('country-stats-legend-slot');
        if (!slot) return;
        const note = document.createElement('div');
        note.className = 'legend-attribution';
        note.style.cssText = 'font-size:10px;color:#ddd;max-width:200px;line-height:1.25;margin-top:2px;';
        note.innerHTML = attributionHtml(data.indicator);
        slot.appendChild(note);
    };

    const opacity = (cfg) => opacityUniform(cfg, 0.7);

    const popupHtml = (code, { name }) => {
        const entry = data?.countries?.[code];
        return buildPopupHtml({
            title: { text: name },
            blocks: [
                { type: 'text', text: countryLine(entry, data.indicator) },
                { type: 'notice', raw: true, color: '#6c757d', text: attributionHtml(data.indicator) },
            ],
        });
    };

    // Draws whatever `selected` is now: its colours and legend, or -- for "None" --
    // nothing at all but the picker.
    const applySelection = async (cfg) => {
        data = selected ? await fetchData(cfg) : null;
        const visibility = data ? 'visible' : 'none';
        for (const id of [fillId, lineId]) {
            if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility);
        }
        if (data && map.getLayer(fillId)) {
            map.setPaintProperty(fillId, 'fill-color', fillColorExpression(data));
            map.setPaintProperty(fillId, 'fill-opacity', opacity(cfg));
        }
        showLegend(cfg);
        // Changing indicator (or "None") closes a popup that belongs to the previous one.
        inspector?.dataChanged();
    };

    const onPick = async (id) => {
        selected = id;
        userPicked = true;
        picker.select.disabled = true;
        try { await applySelection(currentCfg); }
        catch (err) { console.warn('[country_stats] indicator fetch failed', err); }
        finally { picker.select.disabled = false; }
    };

    // The picker shows only while a group is on; it re-reads the groups (and, until
    // the viewer has picked something themselves, the starting indicator) on every
    // settings change.
    const syncPicker = (cfg) => {
        const groups = pickerGroups(catalog, cfg);
        const offered = groups.some((g) => g.indicators.some((i) => i.id === selected));
        if (!userPicked || (selected && !offered)) selected = initialIndicator(catalog, cfg);
        if (!groups.length) {
            picker?.wrap.remove();
            return;
        }
        if (!picker) picker = buildPicker(onPick);
        const host = document.getElementById('basemap-picker-host') || document.body;
        if (!picker.wrap.isConnected) host.appendChild(picker.wrap);
        fillPickerOptions(picker.select, groups, selected);
    };

    const mount = async (cfg) => {
        currentCfg = cfg;
        syncPicker(cfg);
        geometry = geometry || await fetchOrThrow(`${window.MAP_UI}/geo/countries_50m.json`);
        if (map.getSource(sourceId)) return;          // guard against races
        map.addSource(sourceId, { type: 'geojson', data: geometry });
        map.addLayer({
            id: fillId, type: 'fill', source: sourceId,
            paint: { 'fill-color': NO_DATA_COLOR, 'fill-opacity': opacity(cfg) },
        });
        map.addLayer({
            id: lineId, type: 'line', source: sourceId,
            paint: { 'line-color': OUTLINE_COLOR, 'line-width': 0.5 },
        });
        // Off while "None" is picked: no data, so no badge and no popup.
        inspector = attachChoroplethInspector(map, {
            id: 'country-stats', fillLayerId: fillId, codeProp: 'code', geometry,
            hasData: (code) => Boolean(data?.countries?.[code]),
            popupHtml, badgeSpec,
        });
        await applySelection(cfg);
    };

    const refresh = async (cfg) => {
        currentCfg = cfg;
        syncPicker(cfg);
        await applySelection(cfg);
    };

    const unmount = () => {
        inspector?.destroy();
        inspector = null;
        legend.removeLegend();
        picker?.wrap.remove();
        for (const id of [lineId, fillId]) {
            if (map.getLayer(id)) map.removeLayer(id);
        }
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, {
        sectionKey: 'country_stats', initialConfig: config, mount, refresh, unmount, refreshMs: 60 * 60000,
    });
}
