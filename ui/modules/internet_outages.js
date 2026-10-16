// Internet Outages -- a choropleth: every country with an outage IODA (Georgia Tech's Internet
// Outage Detection and Analysis) currently sees, or saw within the grace period, is shaded
// over the bundled Natural Earth country polygons, by how many independent signals agree
// (routing, active probing, Google Transparency Report) -- corroboration, not IODA's raw
// scores, which aren't comparable between signals. Ongoing outages are drawn strong, ended
// ones lighter. Everything is read from our own /api/internet_outages route -- the browser
// never calls IODA.
import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, escapeHtml, buildPopupHtml } from './_feedhelpers.js';
import { replaceSlot, removeLegend } from './_legend.js';

export const ATTRIBUTION = 'Source: IODA, Georgia Tech';
export const CAVEAT =
    'Automated detection, may include false positives; an outage is not necessarily a shutdown.';

const SEVERITY_COLOR = { 1: '#f7e463', 2: '#f39c12', 3: '#d62828' };
const NO_FILL = 'rgba(0,0,0,0)';
const ONGOING_OPACITY = 0.75;
const ENDED_OPACITY = 0.3;
const SIGNAL_NAMES = {
    bgp: 'Routing (BGP)',
    'ping-slash24': 'Active probing',
    gtr: 'Google Transparency Report',
    'merit-nt': 'Network telescope',
};
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const IODA_COUNTRY_URL = 'https://ioda.inetintel.cc.gatech.edu/country/';

export function severityColor(severity) {
    return SEVERITY_COLOR[Math.min(Math.max(severity, 1), 3)];
}

export function severityLabel(severity) {
    return severity === 1 ? '1 signal' : `${severity} signals`;
}

export function signalLabel(signal) {
    return SIGNAL_NAMES[signal] || signal;
}

export function formatDuration(seconds) {
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${minutes % 60}m`;
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

const pad = (n) => String(n).padStart(2, '0');

function formatTime(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()} `
        + `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

// ---- Map expressions: one per paint property, all keyed on the polygons' two-letter code ----

function matchExpression(countries, valueOf, fallback) {
    const pairs = Object.entries(countries).flatMap(([code, c]) => [code, valueOf(c)]);
    // 'match' needs at least one label/output pair.
    return pairs.length ? ['match', ['get', 'iso2'], ...pairs, fallback] : fallback;
}

export function fillColorExpression(countries) {
    return matchExpression(countries, (c) => severityColor(c.severity), NO_FILL);
}

export function fillOpacityExpression(countries) {
    return matchExpression(countries, (c) => (c.ongoing ? ONGOING_OPACITY : ENDED_OPACITY), 0);
}

// Only affected countries are drawn at all, so hovering any other country shows nothing.
export function affectedFilter(countries) {
    const codes = Object.keys(countries);
    return codes.length ? ['in', ['get', 'iso2'], ['literal', codes]] : ['==', ['get', 'iso2'], ''];
}

// ---- Popup and legend ----------------------------------------------------------------

function signalRow(s) {
    const when = formatTime(s.start);
    const length = s.ongoing ? `ongoing ${formatDuration(s.duration_s)}` : `lasted ${formatDuration(s.duration_s)}`;
    return { label: signalLabel(s.signal), value: `${when} · ${length}`, width: 150 };
}

// Only ever link out to IODA, and only for a plain two-letter country code.
function iodaLinkHtml(code) {
    if (!/^[A-Z]{2}$/.test(code)) return '';
    return `<a href="${IODA_COUNTRY_URL}${code}" target="_blank" rel="noopener noreferrer">IODA dashboard</a>`;
}

export function popupHtml(code, entry) {
    const rows = entry.signals.map(signalRow);
    const link = iodaLinkHtml(code);
    if (link) rows.push({ label: 'More', value: link, raw: true, width: 150 });
    return buildPopupHtml({
        title: { text: entry.name, variant: 'alert' },
        subtitle: escapeHtml(`Internet outage · ${severityLabel(entry.severity)} agree${entry.severity === 1 ? 's' : ''}`),
        blocks: [
            { type: 'divider' },
            { type: 'rows', rows },
            { type: 'notice', text: ATTRIBUTION, color: '#888' },
            { type: 'notice', text: CAVEAT, color: '#888' },
        ],
        fontSize: 12,
    });
}

export function legendItems() {
    return [1, 2, 3].map((n) => ({ color: severityColor(n), label: severityLabel(n) }));
}

const LEGEND_SLOT = 'internet-outages-legend-slot';

function addLegend() {
    replaceSlot(LEGEND_SLOT, (slot) => {
        const title = document.createElement('div');
        title.textContent = 'Internet outage';
        title.style.cssText = 'font-size:11px;font-weight:600;color:#eee;margin-bottom:2px;';
        slot.appendChild(title);
        for (const { color, label } of legendItems()) {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:10px;color:#ddd;';
            const swatch = document.createElement('span');
            swatch.style.cssText = `width:12px;height:12px;border-radius:2px;background:${color};opacity:${ONGOING_OPACITY};`;
            row.append(swatch, label);
            slot.appendChild(row);
        }
        const note = document.createElement('div');
        note.style.cssText = 'font-size:10px;color:#ddd;margin-top:2px;max-width:200px;line-height:1.25;';
        note.textContent = 'Lighter: ended recently';
        slot.appendChild(note);
    });
}

// ---- Layer ------------------------------------------------------------------------

export function loadLayer(map, config) {
    const sourceId = 'internet-outages-source';
    const fillId = 'internet-outages-fill';
    const lineId = 'internet-outages-line';
    let stopPopup = null;
    let geometry = null;      // fetched once; the polygons never change
    let countries = {};

    const fetchData = (cfg) => fetchOrThrow(
        `${window.WM_API}/internet_outages/countries?grace_days=${cfg.grace_days ?? 1}&t=${Date.now()}`,
    ).then((d) => d.countries || {});

    const paint = () => {
        if (!map.getLayer(fillId)) return;
        map.setFilter(fillId, affectedFilter(countries));
        map.setFilter(lineId, affectedFilter(countries));
        map.setPaintProperty(fillId, 'fill-color', fillColorExpression(countries));
        map.setPaintProperty(fillId, 'fill-opacity', fillOpacityExpression(countries));
    };

    const popupFor = (f) => {
        const code = f.properties.iso2;
        return countries[code] ? popupHtml(code, countries[code]) : '';
    };

    const mount = async (cfg) => {
        countries = await fetchData(cfg);
        geometry = geometry || await fetchOrThrow(`${window.MAP_UI}/geo/countries_50m.json`);
        if (map.getSource(sourceId)) return;          // guard against races
        map.addSource(sourceId, { type: 'geojson', data: geometry });
        map.addLayer({
            id: fillId, type: 'fill', source: sourceId,
            filter: affectedFilter(countries),
            paint: { 'fill-color': fillColorExpression(countries), 'fill-opacity': fillOpacityExpression(countries) },
        });
        map.addLayer({
            id: lineId, type: 'line', source: sourceId,
            filter: affectedFilter(countries),
            paint: { 'line-color': 'rgba(255,255,255,0.5)', 'line-width': 0.6 },
        });
        // 'move': neighbouring countries share one fill layer, so mouseenter alone wouldn't
        // update crossing a border.
        stopPopup = hoverPopup(map, fillId, { html: popupFor, maxWidth: '320px', event: 'move' });
        addLegend();
    };

    const refresh = async (cfg) => {
        countries = await fetchData(cfg);
        paint();
    };

    const unmount = () => {
        stopPopup?.();
        removeLegend(LEGEND_SLOT);
        for (const id of [lineId, fillId]) {
            if (map.getLayer(id)) map.removeLayer(id);
        }
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, {
        sectionKey: 'internet_outages', initialConfig: config, mount, refresh, unmount, refreshMs: 5 * 60000,
    });
}
