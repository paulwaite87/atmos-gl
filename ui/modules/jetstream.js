import { createCurrentParticleGLLayer } from './_currentparticles_gl.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';

// Backend JetStreamUpdater.VMAX (m/s). Texture is R=U, G=V encoded as
// channel*(2*vmax)-vmax, same convention as wind/currents.
const VMAX = 120.0;

// Mirrors JetStreamUpdater.PALETTES on the backend (tasks/jetstream.py) so the
// particles' speed tint and the colourbar key agree.
const PALETTES = {
    stratosphere: [[0.05, 0.05, 0.35], [0, 0.65, 0.9], [0.85, 0.95, 1.0]],
    aurora: [[0.0, 0.15, 0.12], [0.1, 0.9, 0.45], [0.65, 0.2, 0.95]],
    inferno: [[0.08, 0.0, 0.02], [0.85, 0.3, 0.0], [1.0, 0.9, 0.4]],
};

// Live feedback: even at level_of_detail=1 (the lowest tier), the engine's own default
// LOD_COUNT ({1:4000, 2:9000, 3:18000}, tuned for currents spread across open ocean)
// packed jet-core particles densely enough that overlapping trails read as longer/
// bunched than they actually are. Same problem wind hit and fixed the same way (its
// own dedicated, lower LOD_COUNT) -- halved here as a first pass, per that live call.
export const LOD_COUNT = { 1: 2000, 2: 4500, 3: 9000 };

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
// Second live-tuning pass: the first fix (wind's own 3.0e-5..3.0e-4 range) fixed the
// jitter and made individual particles visible, but was still judged too long at the
// default (trail_length=50 -> 1.65e-4). Rescaled so THAT value becomes the slider's
// MAX (trail_length=100), not its default -- i.e. multiplied wind's whole range by
// 1.65e-4/3.0e-4 (0.55), keeping the same proportional shape (10x span, same relative
// feel) at roughly half the absolute length throughout.
// Clamp shape matches currents' (0-100 slider), not wind's (10-100 slider) -- jetstream
// reuses currents' _TRAIL_LENGTH spec (see #184), which allows 0.
export function hFromConfig(cfg) {
    const t = Number(cfg.trail_length);
    const frac = (t >= 0 && t <= 100) ? t / 100 : 0.5;
    return 1.65e-5 + frac * (1.65e-4 - 1.65e-5);
}

// flow_coherence_radius (config, 0-10, added alongside this fix): direction-coherence
// smoothing, reusing wind's own mechanism/field -- see wind.js's identical reader.
// jetstream reads the same noisy 0.25deg GFS grid wind does (unlike currents' smooth
// RTOFS source, which never sets this), so the same smoothing need applies.
export function coherenceRadius(cfg) {
    const v = Number(cfg.flow_coherence_radius);
    return (isFinite(v) && v > 0) ? v : 0;
}

// Resolve the configured palette (falling back to the default for an unset/unknown
// name), read fresh from whatever cfg is passed in -- NOT captured once at mount. The
// engine calls colormap(cfg) with the live config on every refresh (see
// _currentparticles_gl.js's refresh()); a bare `config.palette` closed over at
// loadLayer()-time would silently ignore a later palette change (the bug this fixes).
export function paletteFor(cfg) {
    return (cfg.palette && PALETTES[cfg.palette]) ? cfg.palette : 'stratosphere';
}

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'jetstream-legend-slot';

    const addLegend = (cfg) => {
        showLegend(slotId, `${window.MAP_UI}/${keyFilename(cfg.outfile)}?t=${Date.now()}`);
    };
    const clearLegend = () => removeLegend(slotId);

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
        colormap: (cfg) => buildLUT(paletteFor(cfg)),
        maxSpeedColor: () => VMAX,
        landReset: () => 0.0,             // jet-core wind blows over land AND ocean
        speedFromConfig,
        hFromConfig,
        coherenceRadius,
        lodCount: LOD_COUNT,
        // thicknessFromConfig/tailFadeEnd: not overridden -- the engine's own defaults
        // already match currents' choice not to override them either (jetstream reuses
        // currents' raw-px trail_thickness slider shape, see #184).
        onMount: addLegend,
        onRefresh: addLegend,
        onUnmount: clearLegend,
        // Palette changes never touch the velocity texture (colour is applied entirely
        // client-side), so the default imageUrl regen chase can't detect that the
        // legend needs re-fetching -- keyUrl gives it its own independent chase.
        keyUrl: (cfg) => `${window.MAP_UI}/${keyFilename(cfg.outfile)}`,
    });

    return () => {
        try { stopParticles && stopParticles(); } catch {}
        try { clearLegend(); } catch {}
    };
}
