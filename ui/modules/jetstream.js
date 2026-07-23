import { createCurrentParticleGLLayer } from './_currentparticles_gl.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

// Backend JetStreamUpdater.VMAX (m/s). Texture is R=U, G=V encoded as
// channel*(2*vmax)-vmax, same convention as wind/currents.
const VMAX = 120.0;

// Mirrors JetStreamUpdater.PALETTES on the backend (tasks/jetstream.py) so the
// particles' speed tint and the colourbar key agree. Provisional -- a single
// "upper atmosphere" ramp for now; more named options can be added the same way
// currents' four grew, once this first one has been seen live.
const PALETTES = {
    stratosphere: [[0.05, 0.05, 0.35], [0, 0.65, 0.9], [0.85, 0.95, 1.0]],
};

export function buildLUT(paletteName) {
    const pal = PALETTES[paletteName] || PALETTES.stratosphere;
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) {
        const fp = (i / 255) * (pal.length - 1);
        const lo = Math.floor(fp), hi = Math.min(lo + 1, pal.length - 1), f = fp - lo;
        const o = i * 4;
        for (let j = 0; j < 3; j++)
            lut[o + j] = Math.round((pal[lo][j] * (1 - f) + pal[hi][j] * f) * 255);
        lut[o + 3] = 255;
    }
    return lut;
}

// particle_speed (0-100) -> the trail engine's per-frame advection multiplier.
// PROVISIONAL, first-pass estimate (no live-tuning history yet, unlike wind's/
// currents' own heavily-tuned formulas -- see their comments in wind.js/currents.js).
// Linear, like wind's own mapping -- currents' is quadratic instead, so "the shape"
// isn't actually consistent enough between the two existing consumers to be worth
// lifting into a shared helper; each is a one-line expression, not real control flow.
export function speedFromConfig(cfg) {
    const ui = Number(cfg.particle_speed);
    const v = isFinite(ui) ? Math.min(100, Math.max(0, ui)) : 50;
    return (v / 100) * 0.2;
}

// trail_length (0-100) -> the trail engine's per-segment integration arc (u_H).
// PROVISIONAL, first-pass estimate -- same caveat as speedFromConfig above.
export function hFromConfig(cfg) {
    const t = Number(cfg.trail_length);
    const frac = (t >= 0 && t <= 100) ? t / 100 : 0.5;
    return 2.0e-4 + frac * (1.2e-3 - 2.0e-4);
}

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'jetstream-legend-slot';

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(cfg.outfile)}?t=${Date.now()}`);
    };
    const clearLegend = () => removeLegend(slotId);

    const palette = config.palette && PALETTES[config.palette] ? config.palette : 'stratosphere';

    // Particle-only, speed-colored (no heatmap -- see tasks/jetstream.py's docstring),
    // via the same shared engine wind and currents already use. No custom hourDataUrl/
    // backfillKey: jetstream's forecast hours are GFS-timeline-relative 1:1, same as
    // wind/isobars, so the engine's own default (cfg.outfile-based) URL already works
    // unmodified -- unlike currents, which needs RTOFS-hour reconciliation.
    const stopParticles = createCurrentParticleGLLayer(map, {
        sectionKey: 'jetstream',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmax: VMAX,                       // matches backend JetStreamUpdater.VMAX
        colormap: () => buildLUT(palette),
        maxSpeedColor: () => VMAX,
        landReset: () => 0.0,             // jet-core wind blows over land AND ocean
        speedFromConfig,
        hFromConfig,
        // thicknessFromConfig/tailFadeEnd/lodCount/coherenceRadius: not overridden --
        // the engine's own defaults already match currents' choice not to override
        // them either (jetstream reuses currents' raw-px trail_thickness slider shape,
        // see #184). Jet-core wind is a large, coherent synoptic-scale feature (unlike
        // turbulent surface wind), so it's not assumed to need wind's coherenceRadius
        // smoothing -- revisit live if the ribbons read as jittery.
        onMount: addLegend,
        onRefresh: addLegend,
        onUnmount: clearLegend,
    });

    return () => {
        try { stopParticles && stopParticles(); } catch {}
        try { clearLegend(); } catch {}
    };
}
