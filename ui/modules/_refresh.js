/**
 * Keep a raster image layer in live sync with its backend `enabled` flag.
 * config handed to loadLayer is a load-time snapshot, so we re-read live config.
 * After (re)mounting we poll fast until the PNG actually exists, then refresh slowly.
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
        console.log(`[${sectionKey}] enabled — mounting; awaiting image.`);
        return true;                                       // readiness handled next tick
    };

    const onDisable = () => {
        unmount();
        imageReady = false; awaitingRegen = false;
        console.log(`[${sectionKey}] disabled — layer removed.`);
    };

    const onTick = async (section, data) => {
        const globals = pickGlobals(data);
        if (!imageReady) {
            const exists = await imageExists(section);
            if (exists) {
                refresh(section, globals);
                imageReady = true;
                lastRefresh = Date.now();
                lastSig = sigOf(section, globals);
                console.log(`[${sectionKey}] image ready — layer shown.`);
            }
        } else {
            const sig = sigOf(section, globals);
            if (sig !== lastSig) {                    // settings changed
                refresh(section, globals);            // apply frontend-side change now
                lastSig = sig;
                lastRefresh = Date.now();
                awaitingRegen = true;
                regenDeadline = Date.now() + regenWaitMs;
                baselineMtime = await imageMtime(section);
            } else if (awaitingRegen) {
                const m = await imageMtime(section);
                if (m && m > baselineMtime) {         // backend produced a fresh render
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
                refresh(section, globals);            // unchanged config: slow cadence
                lastRefresh = Date.now();             // (picks up regenerated PNGs)
            }
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
