/**
 * Keep a raster image layer in live sync with its backend `enabled` flag.
 * config handed to loadLayer is a load-time snapshot, so we re-read live config.
 * After (re)mounting we poll fast until the PNG actually exists, then refresh slowly.
 * A settings change (signature change) always applies immediately, even mid-chase --
 * the image-existence chase tracks DATA freshness and must never gate frontend-only
 * config like opacity from taking effect.
 *
 * globalKeys: extra top-level config sections (e.g. ['animation']) to read each
 * poll and pass through to mount/refresh as a second `globals` arg. They are
 * folded into the change signature, so editing a shared section live-updates
 * every layer that watches it. initialGlobals seeds the snapshot mount.
 *
 * The poll loop, enabled/disabled dispatch, busy-lock, and teardown are owned by the
 * shared reconcileLoop (_reconcile.js, architecture review candidate "unify the two
 * reconcile engines"); this module supplies only what's specific to raster layers --
 * the image-existence chase and regen-detection sequence. (A prior viewportRender
 * mode -- on-demand re-render of the current map bounds -- was dropped in that same
 * pass: zero live callers ever supplied it.)
 */
import { reconcileLoop } from './_reconcile.js';

export function liveLayerSync(map, {
    sectionKey, initialConfig, mount, refresh, unmount, imageUrl,
    onMissing = null,        // optional: called with cfg when the probe HEAD 404s, so the
                             // layer can flag demand-driven backfill for the missing hour
    // Optional: (cfg) => the backend-rendered legend/colourbar-key PNG's URL, if this
    // layer has one. Lives at a DIFFERENT path than imageUrl and isn't gated on
    // forecast-hour freshness, so it needs its own independent regen chase -- a config
    // change that only affects the key (e.g. a client-side-colormap layer's palette,
    // which never touches the data image's own mtime) would otherwise go undetected by
    // the imageUrl-only chase below and sit stale until the slow refreshMs fallback.
    // null (default): no key image, no extra chase -- existing callers unaffected.
    keyUrl = null,
    syncMs = 20000, refreshMs = 300000,
    globalKeys = [], initialGlobals = {},
    regenWaitMs = 120000,
}) {
    let imageReady = false;
    let lastRefresh = 0;
    let lastSig = '';                                 // JSON of last-seen section + globals
    let awaitingRegen = false;
    let regenDeadline = 0;
    let baselineMtime = 0;
    let awaitingKeyRegen = false;
    let keyRegenDeadline = 0;
    let keyBaselineMtime = 0;

    // The shared reconcileLoop's initial dispatch only knows this layer's OWN
    // section (it has no concept of globalKeys), so `data` won't carry the other
    // global sections yet on that first call — fall back to initialGlobals there.
    // Every later, poll-driven call passes the full fetched config blob, so real
    // (possibly-changed) values take over from the second call onward.
    const pickGlobals = (data) => {
        const g = {};
        for (const k of globalKeys) {
            const v = data ? data[k] : undefined;
            g[k] = v !== undefined ? v : initialGlobals[k];
        }
        return g;
    };
    const sigOf = (section, globals) => JSON.stringify([section, globals]);

    // Append a cache-busting probe param with the correct separator: imageUrl already
    // carries a "?t=..." for some layers (e.g. currents), so a bare "?probe=" produced a
    // malformed double-"?" URL. Use "&" when a query string is already present.
    const withProbe = (url) => url + (url.includes('?') ? '&' : '?') + 'probe=' + Date.now();

    // Last-Modified of the served image as a millisecond timestamp (0 if unavailable).
    const imageMtime = async (cfg) => {
        if (!imageUrl) return 0;
        const url = imageUrl(cfg);
        if (!url) return 0;                 // URL not resolvable yet (e.g. reconciler) -> skip
        try {
            const r = await fetch(withProbe(url), { method: 'HEAD' });
            if (!r.ok) {
                if (r.status === 404 && onMissing) {
                    try { onMissing(cfg); } catch (e) { /* best-effort */ }
                }
                return 0;
            }
            const lm = r.headers.get('Last-Modified');
            const t = lm ? Date.parse(lm) : NaN;
            return Number.isNaN(t) ? 0 : t;
        } catch { return 0; }
    };

    // Last-Modified of the served key image (0 if unavailable) -- same shape as
    // imageMtime but never triggers onMissing: a missing legend must never be treated
    // as "the forecast hour is missing" (that's imageUrl's/onMissing's job, driving
    // demand-driven backfill), so any non-ok status just means "no evidence yet".
    const keyMtime = async (cfg) => {
        if (!keyUrl) return 0;
        const url = keyUrl(cfg);
        if (!url) return 0;
        try {
            const r = await fetch(withProbe(url), { method: 'HEAD' });
            if (!r.ok) return 0;
            const lm = r.headers.get('Last-Modified');
            const t = lm ? Date.parse(lm) : NaN;
            return Number.isNaN(t) ? 0 : t;
        } catch { return 0; }
    };

    const imageExists = async (cfg) => {
        if (!imageUrl) return true;                       // no probe supplied -> assume present
        const url = imageUrl(cfg);
        if (!url) return false;             // URL not resolvable yet -> not ready, don't flag
        try {
            const r = await fetch(withProbe(url), { method: 'HEAD' });
            if (!r.ok && r.status === 404 && onMissing) {
                try { onMissing(cfg); } catch (e) { /* best-effort backfill flag */ }
            }
            return r.ok;
        } catch { return false; }
    };

    const onEnable = async (section, data) => {
        const globals = pickGlobals(data);
        mount(section, globals);
        imageReady = false;
        lastRefresh = Date.now(); lastSig = sigOf(section, globals);
        awaitingRegen = false;
        awaitingKeyRegen = false;
        console.log(`[${sectionKey}] enabled — mounting; awaiting image.`);
        return true;                                       // readiness handled next tick
    };

    const onDisable = () => {
        unmount();
        imageReady = false; awaitingRegen = false; awaitingKeyRegen = false;
        console.log(`[${sectionKey}] disabled — layer removed.`);
    };

    const onTick = async (section, data) => {
        const globals = pickGlobals(data);
        const sig = sigOf(section, globals);
        const changed = sig !== lastSig;
        if (changed) {
            // Settings changed (e.g. opacity) -- apply immediately, independent of
            // imageReady. imageReady tracks DATA freshness (does this hour's PNG exist
            // yet); it must not gate frontend-only settings from taking effect, or a
            // layer stuck waiting on a not-yet-rendered hour ignores live config changes.
            refresh(section, globals);
            lastSig = sig;
            lastRefresh = Date.now();
            if (imageReady) {
                awaitingRegen = true;
                regenDeadline = Date.now() + regenWaitMs;
                baselineMtime = await imageMtime(section);
            }
            if (keyUrl) {
                awaitingKeyRegen = true;
                keyRegenDeadline = Date.now() + regenWaitMs;
                keyBaselineMtime = await keyMtime(section);
            }
        } else if (!imageReady) {
            const exists = await imageExists(section);
            if (exists) {
                refresh(section, globals);
                imageReady = true;
                lastRefresh = Date.now();
                console.log(`[${sectionKey}] image ready — layer shown.`);
            }
        } else if (awaitingRegen) {
            const m = await imageMtime(section);
            if (m && m > baselineMtime) {             // backend produced a fresh render
                refresh(section, globals);
                awaitingRegen = false;
                lastRefresh = Date.now();
                console.log(`[${sectionKey}] backend re-render detected — applied.`);
            } else if (m === 0) {
                refresh(section, globals);
                lastRefresh = Date.now();
            }
            if (Date.now() >= regenDeadline) awaitingRegen = false;
        } else if (Date.now() - lastRefresh >= refreshMs) {
            refresh(section, globals);                // unchanged config: slow cadence
            lastRefresh = Date.now();                 // (picks up regenerated PNGs)
        }

        // Independent key-image regen chase (opt-in via keyUrl) -- see its docstring
        // above. Runs alongside (not instead of) the branches above; skipped on the
        // SAME tick a change just armed it, since keyBaselineMtime was only just set.
        if (!changed && awaitingKeyRegen) {
            const km = await keyMtime(section);
            if (km && km > keyBaselineMtime) {
                refresh(section, globals);
                awaitingKeyRegen = false;
                lastRefresh = Date.now();
                console.log(`[${sectionKey}] backend key re-render detected — applied.`);
            }
            if (Date.now() >= keyRegenDeadline) awaitingKeyRegen = false;
        }
    };

    return reconcileLoop(map, {
        sectionKey,
        initialConfig,
        syncMs,
        onEnable,
        onDisable,
        onTick,
    });
}
