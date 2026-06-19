import { createWindParticleGLLayer } from './_windparticles_gl.js';

// Wind-speed colour ramp, calm -> strong, sampled across [0, max_speed_color] m/s.
const PALETTE = [
    [0.25, 0.30, 0.60],   // calm   - deep blue
    [0.15, 0.60, 0.85],   // light  - cyan-blue
    [0.20, 0.75, 0.45],   // breeze - green
    [0.95, 0.90, 0.30],   // fresh  - yellow
    [0.95, 0.55, 0.20],   // strong - orange
    [0.90, 0.20, 0.20],   // gale   - red
    [0.75, 0.25, 0.85],   // storm  - violet
];

function buildLUT() {
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const fp = (i / 255) * (PALETTE.length - 1);
        const lo = Math.floor(fp), hi = Math.min(lo + 1, PALETTE.length - 1), f = fp - lo;
        const o = i * 4;
        for (let j = 0; j < 3; j++) {
            lut[o + j] = Math.round((PALETTE[lo][j] * (1 - f) + PALETTE[hi][j] * f) * 255);
        }
        lut[o + 3] = 255;
    }
    return lut;
}

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'wind-legend-slot';
    const rgbCss = (c) => `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)})`;
    const gradient = () => PALETTE
        .map((c, i) => `${rgbCss(c)} ${(i / (PALETTE.length - 1) * 100).toFixed(1)}%`)
        .join(', ');

    const addLegend = (cfg) => {
        const stack = document.getElementById('legend-stack');
        if (!stack) return;
        document.getElementById(slotId)?.remove();
        const vmaxKph = Number(cfg.max_speed_color) > 0 ? Number(cfg.max_speed_color) : 100;
        const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(vmaxKph * f));

        const slot = document.createElement('div');
        slot.id = slotId;
        slot.className = 'legend-slot';
        slot.innerHTML = `
            <div style="font-size:11px;color:#fff;font-weight:600;margin-bottom:3px;">Wind speed (km/h)</div>
            <div style="height:10px;border-radius:2px;background:linear-gradient(to right, ${gradient()});"></div>
            <div style="display:flex;justify-content:space-between;font-size:10px;color:rgba(255,255,255,0.8);margin-top:2px;">
                ${ticks.map(t => `<span>${t}</span>`).join('')}
            </div>`;
        stack.appendChild(slot);
    };
    const removeLegend = () => document.getElementById(slotId)?.remove();

    return createWindParticleGLLayer(map, {
        sectionKey: 'wind',
        initialConfig: config,
        vmax: 40.0,                   // must match backend VMAX_WIND
        colormap: () => buildLUT(),   // speed LUT (palette fixed for now)
        // max_speed_color is in km/h (user-facing); convert to m/s for the speed shader.
        maxSpeedColor: (cfg) => (Number(cfg.max_speed_color) > 0 ? Number(cfg.max_speed_color) : 100) / 3.6,
        // Boundary-clump fix: wind particles that drift into land / no-data / data-edge
        // cells stall at ~0 velocity and pile into static clumps. Recycle the near-dead
        // ones FAST (big bump) but only within a narrow 0..3 m/s band (dropSpeed) so
        // genuinely light-but-real winds above that keep full density. Config can still
        // override via drop_rate_bump / drop_speed. (Currents use a separate engine; waves
        // keep the gentler engine defaults — these overrides are wind-only.)
        dropBump: (cfg) => (cfg.drop_rate_bump != null ? Number(cfg.drop_rate_bump) : 0.08),
        dropSpeed: (cfg) => (cfg.drop_speed != null ? Number(cfg.drop_speed) : 3.0),
        onMount: addLegend,
        onRefresh: addLegend,         // re-draw if max_speed_color changed
        onUnmount: removeLegend,      // animated layer only -> legend hidden with barbs
        // Tunables fall through to _windparticles defaults; override via wind config:
        //   particle_count, particle_speed, trail_fade, particle_size,
        //   drop_rate, drop_rate_bump, max_speed_color, particle_alpha
    });
}