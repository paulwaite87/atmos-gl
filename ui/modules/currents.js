import { createFillLayer } from './_webglfill.js';
import { createCurrentParticleGLLayer } from './_currentparticles_gl.js';
import { timeline } from './timeline.js';

// Backend VMAX_CURRENT (m/s). Texture is R=U, G=V encoded as channel*(2*vmax)-vmax.
const VMAX = 2.5;

// Current-speed colour ramps (mirror CurrentsUpdater.PALETTES on the backend so the
// fill, the particles' speed tint, and the colourbar key all agree).
const PALETTES = {
    thermal_red:   [[0.65,0,0],[1,0.25,0],[1,0.85,0],[1,1,1]],
    electric_blue: [[0,0.35,0.55],[0,0.85,1],[0.75,1,1]],
    toxic_neon:    [[0,0.45,0.15],[0.25,1,0],[0.95,1,0.3]],
    cyberpunk:     [[0.45,0,0.45],[1,0,0.55],[0,1,0.75]],
};

function buildLUT(paletteName) {
    const pal = PALETTES[paletteName] || PALETTES.thermal_red;
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

// ---- valid_time reconciliation -------------------------------------------------
// Currents come from the RTOFS run (its own absolute forecast-hour numbering); the
// scrubber timeline is GFS-relative. We translate the timeline's CURRENT hour to the
// RTOFS forecast hour with the same wall-clock (valid_time), using the `currents`
// block from /api/forecast_state. Falls back to identity if the block is absent.
function makeReconciler() {
    let rtofs = null;           // { hours:[...], validMs:{hour->ms}, sortedByMs:[[ms,hour]...] }
    let fetched = false;

    const load = async () => {
        if (fetched) return;
        fetched = true;
        try {
            const res = await fetch(`${window.WM_API}/forecast_state?t=${Date.now()}`);
            const j = await res.json();
            const c = j?.data?.currents;
            if (c && c.valid_times_utc) {
                const validMs = {};
                const sorted = [];
                for (const [h, iso] of Object.entries(c.valid_times_utc)) {
                    const ms = Date.parse(iso);
                    validMs[h] = ms;
                    sorted.push([ms, parseInt(h, 10)]);
                }
                sorted.sort((a, b) => a[0] - b[0]);
                rtofs = { validMs, sortedByMs: sorted };
            }
        } catch (e) {
            console.warn('[currents] forecast_state currents block unavailable; using identity hours', e);
        }
    };
    load();

    // Map a timeline hour -> RTOFS forecast hour by nearest valid_time.
    const toRtofsHour = (timelineHour) => {
        if (!rtofs) return timelineHour;                         // identity fallback
        const snap = timeline.get();
        const iso = snap.validTimes && snap.validTimes[String(timelineHour)];
        const targetMs = iso ? Date.parse(iso) : null;
        if (targetMs == null) return timelineHour;
        // nearest RTOFS hour by |Δt|
        let best = rtofs.sortedByMs[0], bestDiff = Infinity;
        for (const pair of rtofs.sortedByMs) {
            const d = Math.abs(pair[0] - targetMs);
            if (d < bestDiff) { bestDiff = d; best = pair; }
        }
        return best[1];
    };
    return { toRtofsHour, ready: () => load() };
}

export function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'currents-legend-slot';
    const recon = makeReconciler();

    // currents data URL with RTOFS-hour translation (shared by fill + particles).
    const currentsHourUrl = (cfg, timelineHour, bust) => {
        const base = cfg.outfile.replace(/\.png$/, '');
        const rh = recon.toRtofsHour(timelineHour);
        const f = String(rh).padStart(3, '0');
        return `${window.MAP_UI}/${base}_f${f}_data.png?t=${bust}`;
    };

    // ---- legend (colourbar key PNG written by the backend) ----
    const keyUrlFor = (cfg) => {
        const o = cfg.outfile, i = o.lastIndexOf('.');
        const b = i !== -1 ? o.slice(0, i) : o, e = i !== -1 ? o.slice(i) : '';
        return `${window.MAP_UI}/${b}_key${e}`;
    };
    const addLegend = (cfg) => {
        const stack = document.getElementById('legend-stack');
        if (!stack) return;
        document.getElementById(slotId)?.remove();
        const slot = document.createElement('div');
        slot.id = slotId; slot.className = 'legend-slot';
        const img = document.createElement('img');
        img.src = `${keyUrlFor(cfg)}?t=${Date.now()}`;
        img.style.display = 'block'; img.style.width = '100%';
        slot.appendChild(img); stack.appendChild(slot);
    };
    const removeLegend = () => document.getElementById(slotId)?.remove();

    const palette = config.palette && PALETTES[config.palette] ? config.palette : 'thermal_red';

    // ---- 1) SPEED FILL (underneath): speed = |decode(u,v)|, coloured via LUT ----
    createFillLayer(map, {
        sectionKey: 'currents',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: 0.0, vspan: 1.0,            // value channel unused; we decode u/v ourselves
        opacity: Number(config.alpha) > 0 ? Number(config.alpha) / 100 : 0.6,
        colormap: () => buildLUT(palette),
        hourDataUrl: currentsHourUrl,     // RTOFS-hour translated
        // shade() decodes u/v from R/G directly (same scheme as the particle layer)
        // and colours by speed, so the built-in value/bicubic path is unused here.
        fragmentBody: `
            uniform float u_vmax_current;
            uniform float u_alpha;
            // decode a texel's (u,v) in m/s
            vec2 decodeUV(vec4 t) { return t.rg * (2.0 * u_vmax_current) - u_vmax_current; }
            vec4 shade(float value, vec2 uv) {
                vec4 t0 = texture(u_tex0, uv);
                vec4 t1 = texture(u_tex1, uv);
                if (t0.a < 0.5 || t1.a < 0.5) discard;         // land/no-data
                vec2 vel = mix(decodeUV(t0), decodeUV(t1), u_frac);
                float spd = length(vel);
                float s = clamp(spd / u_vmax_current, 0.0, 1.0);
                vec3 c = texture(u_cmap, vec2(s, 0.5)).rgb;
                return vec4(c, u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_vmax_current: VMAX,
            u_alpha: Number(cfg.alpha) > 0 ? Number(cfg.alpha) / 100 : 0.6,
        }),
        onMount: addLegend,
        onRefresh: addLegend,
        onUnmount: removeLegend,
    });

    // ---- 2) FLOWING TRAIL PARTICLES (on top): each particle draws its recent
    // path as a fading, tapering ribbon along the current — the smooth flowing look.
    createCurrentParticleGLLayer(map, {
        sectionKey: 'currents',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmax: VMAX,                       // matches backend VMAX_CURRENT
        colormap: () => buildLUT(palette),
        maxSpeedColor: () => VMAX,        // speed tint scaled to current speeds
        landReset: () => 1.0,             // currents must NOT flow over land
        hourDataUrl: currentsHourUrl,     // same RTOFS-hour translation
        // trail tunables fall through to config: particle_count, particle_speed,
        // trail_thickness, particle_alpha.
    });
}
