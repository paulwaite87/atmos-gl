import { createFillLayer } from './_webglfill.js';
import { replaceSlot, removeLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';

// GPU scrubber layer in the Ozone mould. The backend derives UVI per forecast hour
// (lib/uv_index.py) and serves the display contract -- value range, WHO bands, and the
// UVI below which nothing is drawn -- via /api/config's scalar_field_specs.uv_index, so
// the colours here can never drift from the static render's.

export const MODELLED_NOTE = 'Modelled from GFS ozone and cloud cover';

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

// "Low 0–2", ..., "Extreme 11+": each band runs up to one below the next band's start.
export function legendItems(bands) {
    return bands.map((b, i) => {
        const next = bands[i + 1];
        const range = next ? `${b.from}–${next.from - 1}` : `${b.from}+`;
        return { color: b.color, label: `${b.name} ${range}` };
    });
}

// 256x1 RGBA LUT over [vmin, vmax]: transparent below `visible_from`, otherwise each
// band's flat colour (hard steps; the GPU's linear filtering softens the one-texel seam).
export function buildBandLUT({ vmin, vmax, visible_from: visibleFrom, bands }) {
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const value = vmin + (i / 255) * (vmax - vmin);
        const o = i * 4;
        if (value < visibleFrom) continue;                 // stays 0,0,0,0
        let band = bands[0];
        for (const b of bands) if (value >= b.from) band = b;
        const [r, g, bl] = rgb(band.color);
        lut[o] = r; lut[o + 1] = g; lut[o + 2] = bl; lut[o + 3] = 255;
    }
    return lut;
}

const LEGEND_SLOT = 'uv-index-legend-slot';

function addLegend(spec, cfg) {
    if (opacityUniform(cfg, 0.7) <= 0) { removeLegend(LEGEND_SLOT); return; }
    replaceSlot(LEGEND_SLOT, (slot) => {
        const title = document.createElement('div');
        title.textContent = 'UV Index';
        title.style.cssText = 'font-size:11px;font-weight:600;color:#eee;margin-bottom:2px;';
        slot.appendChild(title);
        for (const { color, label } of legendItems(spec.bands)) {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:10px;color:#ddd;';
            const swatch = document.createElement('span');
            swatch.style.cssText = `width:12px;height:12px;border-radius:2px;background:${color};`;
            row.append(swatch, label);
            slot.appendChild(row);
        }
        const note = document.createElement('div');
        note.textContent = MODELLED_NOTE;
        note.style.cssText = 'font-size:9px;color:#aaa;margin-top:2px;max-width:200px;line-height:1.25;';
        slot.appendChild(note);
    });
}

export function loadLayer(map, config, fullConfig = {}) {
    const spec = fullConfig.scalar_field_specs.uv_index;
    const { vmin: VMIN, vmax: VMAX } = spec;
    const lut = buildBandLUT(spec);

    createFillLayer(map, {
        sectionKey: 'uv_index',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: VMIN,
        vspan: VMAX - VMIN,
        opacity: 1.0,
        bicubic: false,                        // hard band edges: bicubic would ring at them
        fragmentBody: `
            uniform float u_alpha;
            vec4 shade(float value, vec2 uv) {
                float t = clamp((value - ${VMIN.toFixed(1)}) / ${(VMAX - VMIN).toFixed(1)}, 0.0, 1.0);
                vec4 c = texture(u_cmap, vec2(t, 0.5));
                return vec4(c.rgb, c.a * u_alpha);
            }`,
        customUniforms: (cfg) => ({ u_alpha: opacityUniform(cfg, 0.7) }),
        colormap: () => lut,
        onMount: (cfg) => addLegend(spec, cfg),
        onRefresh: (cfg) => addLegend(spec, cfg),
        onUnmount: () => removeLegend(LEGEND_SLOT),
    });
}
