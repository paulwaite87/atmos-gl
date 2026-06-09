import { createParticleWindLayer } from './_windparticles.js';

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
    createParticleWindLayer(map, {
        sectionKey: 'wind',
        initialConfig: config,
        vmax: 40.0,                   // must match backend VMAX_WIND
        colormap: () => buildLUT(),   // speed LUT (palette fixed for now)
        // Tunables fall through to _windparticles defaults; override via wind config:
        //   particle_count, particle_speed, trail_fade, particle_size,
        //   drop_rate, drop_rate_bump, max_speed_color, particle_alpha
    });
}