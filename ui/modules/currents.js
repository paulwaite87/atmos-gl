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
    let loadPromise = null;

    const load = () => {
        // Memoize: return the SAME in-flight/settled promise so awaiting ready()
        // actually waits for the fetch (a boolean guard would resolve instantly and
        // re-introduce the race that caused 404s on un-translated timeline hours).
        if (loadPromise) return loadPromise;
        loadPromise = (async () => {
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
        })();
        return loadPromise;
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

export async function loadLayer(map, config, fullConfig = {}) {
    const slotId = 'currents-legend-slot';
    const recon = makeReconciler();
    // Wait for the RTOFS hour<->valid_time map (forecast_state) BEFORE creating the
    // layers, so the first texture fetch already translates the timeline hour to the
    // correct RTOFS forecast hour. Otherwise the layers race ahead of the async load,
    // fall back to identity (timeline hour), and request hours that don't exist as
    // files (e.g. f007 when currents files are f032..f042) -> 404 / no data.
    await recon.ready();

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
    const stopFill = createFillLayer(map, {
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
            uniform float u_fill_floor;   // speed (m/s) below which fill is transparent
            uniform float u_fill_knee;    // speed (m/s) at which fill reaches full alpha
            // decode a texel's (u,v) in m/s
            vec2 decodeUV(vec4 t) { return t.rg * (2.0 * u_vmax_current) - u_vmax_current; }
            vec4 shade(float value, vec2 uv) {
                vec4 t0 = texture(u_tex0, uv);
                vec4 t1 = texture(u_tex1, uv);
                if (t0.a < 0.5 || t1.a < 0.5) discard;         // land/no-data
                vec2 vel = mix(decodeUV(t0), decodeUV(t1), u_frac);
                float spd = length(vel);
                // Speed-gated alpha: fully transparent below floor, fading up to knee.
                // This yields "discrete currents over mostly-transparent ocean" instead
                // of a solid wash. Colour still spans the full LUT by speed.
                float aGate = smoothstep(u_fill_floor, u_fill_knee, spd);
                if (aGate <= 0.001) discard;                   // skip dead-slow water
                float s = clamp(spd / u_vmax_current, 0.0, 1.0);
                vec3 c = texture(u_cmap, vec2(s, 0.5)).rgb;
                return vec4(c, u_alpha * aGate);
            }`,
        customUniforms: (cfg) => ({
            u_vmax_current: VMAX,
            u_alpha: Number(cfg.alpha) > 0 ? Number(cfg.alpha) / 100 : 0.6,
            // Tunable via config; defaults chosen for RTOFS (most ocean < 0.2 m/s,
            // currents of interest > ~0.3 m/s, strong jets ~1-2.5 m/s).
            u_fill_floor: Number(cfg.fill_floor) >= 0 ? Number(cfg.fill_floor) : 0.15,
            u_fill_knee: Number(cfg.fill_knee) > 0 ? Number(cfg.fill_knee) : 0.5,
        }),
        onMount: addLegend,
        onRefresh: addLegend,
        onUnmount: removeLegend,
    });

    // ---- 2) PARTICLES (on top): flowing trails advected along the u/v texture ----
    // Dedicated currents trail engine (_currentparticles_gl.js). land-masked
    // (landReset:()=>1.0) so particles stay off the continents.
    const stopParticles = createCurrentParticleGLLayer(map, {
        sectionKey: 'currents',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmax: VMAX,                       // matches backend VMAX_CURRENT
        colormap: () => buildLUT(palette),
        maxSpeedColor: () => VMAX,        // speed tint scaled to current speeds
        landReset: () => 1.0,             // currents must NOT flow over land
        // Map the config UI's 0-100 particle_speed slider to the currents advection
        // multiplier. The pleasant flow we tuned is ~4, so the slider midpoint (50) lands
        // there; 100 -> 8 (fast); 0 -> static particles (the fill shows through, no
        // motion). Default to 50 when unset.
        speedFromConfig: (cfg) => {
            const ui = Number(cfg.particle_speed);
            const v = isFinite(ui) ? Math.min(100, Math.max(0, ui)) : 50;
            return (v / 100) * 8;
        },
        hourDataUrl: currentsHourUrl,     // RTOFS-hour translated (shared with fill)
    });

    // Combined teardown for a basemap style swap: stop both sub-layers and remove the
    // legend. Returned to the host layer registry.
    return () => {
        try { stopParticles && stopParticles(); } catch {}
        try { stopFill && stopFill(); } catch {}
        try { removeLegend(); } catch {}
    };
}