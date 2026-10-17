import { createStaticFillLayer } from './_webglfill.js';
import { replaceSlot, removeLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';

// NOAA OISST sea-ice concentration (tasks/sea_ice.py). The texture holds concentration,
// 0-100 percent, over a FIXED domain, so the pack always reads against the same scale.
// The colour ramp and the ice-edge cutoff live here, so changing them never waits on a
// server render.
export const ENCODE_DOMAIN = [0, 100];
export const MIN_VISIBLE = 15;         // the standard ice-edge definition: nothing drawn below it
export const CREDIT = 'NOAA OISST';

// Sparse ice is translucent pale blue; the dense pack is opaque white.
const PALE_BLUE = [142, 203, 255];
const WHITE = [255, 255, 255];
const EDGE_ALPHA = 0.35;               // how solid the ice reads right at the edge

// 256x1 RGBA LUT over the encode domain: transparent below MIN_VISIBLE, then pale blue
// (EDGE_ALPHA) rising to white (opaque) at 100%.
export function buildSeaIceLUT() {
    const [vmin, vmax] = ENCODE_DOMAIN;
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const pct = vmin + (i / 255) * (vmax - vmin);
        if (pct < MIN_VISIBLE) continue;                    // stays 0,0,0,0
        const t = (pct - MIN_VISIBLE) / (vmax - MIN_VISIBLE);
        const o = i * 4;
        for (let c = 0; c < 3; c++) lut[o + c] = Math.round(PALE_BLUE[c] + t * (WHITE[c] - PALE_BLUE[c]));
        lut[o + 3] = Math.round((EDGE_ALPHA + t * (1 - EDGE_ALPHA)) * 255);
    }
    return lut;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// "2026-10-15" -> "15 Oct 2026". An unreadable value is shown as-is so a stale or odd
// date is never silently hidden.
export function formatDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
    if (!m) return String(iso);
    return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

export function legendLines(meta) {
    const date = meta?.sea_ice?.date;
    return {
        title: 'Sea ice concentration',
        date: date ? `Sea ice · ${formatDate(date)}` : null,
        credit: CREDIT,
    };
}

// The key bar: the same two colours, fully opaque (the layer's own translucency at the
// edge is not part of the scale).
export function legendGradient() {
    const hex = (c) => `#${c.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
    return `linear-gradient(to right, ${hex(PALE_BLUE)} 0%, ${hex(WHITE)} 100%)`;
}

const LEGEND_SLOT = 'sea-ice-legend-slot';

function metaUrl(cfg) {
    const dir = String(cfg.outfile || 'data/sea_ice.png').replace(/[^/]*$/, '');
    return `${window.MAP_UI}/${dir}sea_ice_meta.json?t=${Date.now()}`;
}

async function addLegend(cfg) {
    if (opacityUniform(cfg, 0.7) <= 0) { removeLegend(LEGEND_SLOT); return; }
    const meta = await fetch(metaUrl(cfg)).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
    const lines = legendLines(meta);
    replaceSlot(LEGEND_SLOT, (slot) => {
        const line = (text, css) => {
            const el = document.createElement('div');
            el.textContent = text;
            el.style.cssText = css;
            slot.appendChild(el);
        };
        line(lines.title, 'font-size:11px;font-weight:600;color:#eee;margin-bottom:3px;');
        const bar = document.createElement('div');
        // A thin dark outline: a white bar would otherwise vanish against the legend's own pale text.
        bar.style.cssText = `width:160px;height:10px;border-radius:2px;border:1px solid #555;background:${legendGradient()};`;
        slot.appendChild(bar);
        const scale = document.createElement('div');
        scale.style.cssText = 'display:flex;justify-content:space-between;width:160px;font-size:9px;color:#ddd;';
        for (const t of [`${MIN_VISIBLE}%`, '100%']) {
            const s = document.createElement('span');
            s.textContent = t;
            scale.appendChild(s);
        }
        slot.appendChild(scale);
        if (lines.date) line(lines.date, 'font-size:9px;color:#aaa;margin-top:3px;');
        line(lines.credit, 'font-size:9px;color:#aaa;');
    });
}

export function loadLayer(map, config) {
    const lut = buildSeaIceLUT();

    return createStaticFillLayer(map, {
        sectionKey: 'sea_ice',
        initialConfig: config,
        dataUrl: (cfg) => `${window.MAP_UI}/${cfg.outfile}`,
        physicalDomain: () => ENCODE_DOMAIN,
        fragmentBody: `
            uniform float u_alpha;
            vec4 shade(float value, vec2 uv) {
                float t = clamp((value - u_vmin) / u_span, 0.0, 1.0);
                vec4 c = texture(u_cmap, vec2(t, 0.5));
                return vec4(c.rgb, c.a * u_alpha);
            }`,
        customUniforms: (cfg) => ({ u_alpha: opacityUniform(cfg, 0.7) }),
        colormap: () => lut,
        refreshMs: 3600000,                // the source changes about once a day
        onMount: (cfg) => addLegend(cfg),
        onRefresh: (cfg) => addLegend(cfg),
        onUnmount: () => removeLegend(LEGEND_SLOT),
    });
}
