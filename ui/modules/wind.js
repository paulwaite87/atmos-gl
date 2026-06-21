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

    // Calculate current maximum speed threshold explicitly based on active config
    const currentMaxSpeedMS = (Number(config.max_speed_color) > 0 ? Number(config.max_speed_color) : 100) / 3.6;

    return createWindParticleGLLayer(map, {
        sectionKey: 'wind',
        initialConfig: config,
        // CRITICAL FIX: vmax must match the encoder scale, but let's pass a balanced
        // local threshold constraint so our shader's divergence math is tightly bounded.
        vmax: 40.0,
        colormap: () => buildLUT(),
        maxSpeedColor: () => currentMaxSpeedMS,
        onMount: addLegend,
        onRefresh: addLegend,

        // =====================================================================
        // DIRECT OVERRIDES: WINDY.COM VISUAL BLENDING & LINE THROTTLING
        // =====================================================================
        // Force the core engine (_windparticles_gl.js) to consume these tunables
        // to balance out the particle clustering over high-shear boundaries:

        // 1. Lower global asset counts slightly to avoid overcrowding the layout
        particle_count: (cfg) => {
            const baseCount = Number(cfg.particle_count) > 0 ? Number(cfg.particle_count) : 8000;
            return Math.min(baseCount, 7500); // Caps allocation ceiling to force spacing
        },

        // 2. Increase neighborhood search padding on the GFS texture interpolation.
        // Pushing this past 2.0 texels forces sharp vector transitions to blur into
        // curves instead of computing as hard, pixel-snapped step adjustments.
        wind_smooth: () => 2.5,

        // 3. Accelerate trailing alpha decay rates so that when particles enter
        // a convergence zone, their tails vanish quickly before a visual line forms.
        trail_fade: () => 0.94,

        // 4. Narrow quad dimensions so slow-moving wind streams taper off like fine hairs.
        particle_size: () => 0.8,

        // 5. Calm-zone cleanup configurations:
        calm_speed: () => 1.5,     // Treat everything below 1.5 m/s as a candidate for deletion
        calm_drop: () => 0.35,     // 35% chance to kill and scatter static boundary particles
    });
}
