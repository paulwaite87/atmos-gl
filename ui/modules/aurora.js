import { createStaticFillLayer } from './_webglfill.js';
import { replaceSlot, removeLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';

// NOAA SWPC OVATION aurora probability (tasks/aurora.py). The texture holds the
// probability, 0-100 percent, over a FIXED domain -- never rescaled to the current
// maximum, so a strong night looks stronger than a quiet one. The colour ramp and the
// cutoff live here, so changing them never waits on a server render.
export const ENCODE_DOMAIN = [0, 100];
export const MIN_VISIBLE = 5;          // below this nothing is drawn
const FULL_ALPHA_AT = 15;              // fades in over 5-15% so the oval's edge isn't a hard line
export const CREDIT = 'NOAA SWPC, OVATION';

// green -> yellow -> red, positions in percent (the ramp NOAA's own aurora map uses).
const RAMP = [
    { at: MIN_VISIBLE, color: '#3ddc4a' },
    { at: 40, color: '#f5e216' },
    { at: 100, color: '#e5231f' },
];

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

function rampColor(pct) {
    if (pct <= RAMP[0].at) return rgb(RAMP[0].color);
    for (let i = 1; i < RAMP.length; i++) {
        if (pct <= RAMP[i].at) {
            const [a, b] = [RAMP[i - 1], RAMP[i]];
            const t = (pct - a.at) / (b.at - a.at);
            const [ca, cb] = [rgb(a.color), rgb(b.color)];
            return ca.map((v, k) => Math.round(v + t * (cb[k] - v)));
        }
    }
    return rgb(RAMP[RAMP.length - 1].color);
}

// 256x1 RGBA LUT over the encode domain: transparent below MIN_VISIBLE, fading in to
// opaque by FULL_ALPHA_AT, coloured along the ramp.
export function buildAuroraLUT() {
    const [vmin, vmax] = ENCODE_DOMAIN;
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const pct = vmin + (i / 255) * (vmax - vmin);
        if (pct < MIN_VISIBLE) continue;                    // stays 0,0,0,0
        const [r, g, b] = rampColor(pct);
        const alpha = Math.min(1, (pct - MIN_VISIBLE) / (FULL_ALPHA_AT - MIN_VISIBLE));
        const o = i * 4;
        lut[o] = r; lut[o + 1] = g; lut[o + 2] = b; lut[o + 3] = Math.round(alpha * 255);
    }
    return lut;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// "2026-10-17T05:53:00Z" -> "17 Oct 05:53 UTC". An unreadable value is shown as-is so a
// stale or odd time is never silently hidden.
export function formatTime(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso));
    if (!m) return String(iso);
    return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[4]}:${m[5]} UTC`;
}

export function legendLines(meta) {
    const t = meta?.aurora;
    return {
        title: 'Aurora probability',
        observed: t?.observed ? `Observed ${formatTime(t.observed)}` : null,
        forecast: t?.forecast ? `Forecast ${formatTime(t.forecast)}` : null,
        credit: CREDIT,
    };
}

// The key bar: the same ramp, fully opaque (the layer's own fade-in is not part of the scale).
export function legendGradient() {
    const stops = RAMP.map((s) => `${s.color} ${s.at}%`);
    return `linear-gradient(to right, ${['#3ddc4a 0%', ...stops.slice(1)].join(', ')})`;
}

const LEGEND_SLOT = 'aurora-legend-slot';

function metaUrl(cfg) {
    const dir = String(cfg.outfile || 'data/aurora.png').replace(/[^/]*$/, '');
    return `${window.MAP_UI}/${dir}aurora_meta.json?t=${Date.now()}`;
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
        bar.style.cssText = `width:160px;height:10px;border-radius:2px;background:${legendGradient()};`;
        slot.appendChild(bar);
        const scale = document.createElement('div');
        scale.style.cssText = 'display:flex;justify-content:space-between;width:160px;font-size:9px;color:#ddd;';
        for (const t of [`${MIN_VISIBLE}%`, '40%', '100%']) {
            const s = document.createElement('span');
            s.textContent = t;
            scale.appendChild(s);
        }
        slot.appendChild(scale);
        if (lines.observed) line(lines.observed, 'font-size:9px;color:#aaa;margin-top:3px;');
        if (lines.forecast) line(lines.forecast, 'font-size:9px;color:#aaa;');
        line(lines.credit, 'font-size:9px;color:#aaa;');
    });
}

export function loadLayer(map, config) {
    const lut = buildAuroraLUT();

    return createStaticFillLayer(map, {
        sectionKey: 'aurora',
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
        refreshMs: 120000,                 // NOAA publishes about every five minutes
        onMount: (cfg) => addLegend(cfg),
        onRefresh: (cfg) => addLegend(cfg),
        onUnmount: () => removeLegend(LEGEND_SLOT),
    });
}
