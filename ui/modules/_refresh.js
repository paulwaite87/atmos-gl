/**
 * Keep a raster image layer in live sync with its backend `enabled` flag.
 * config handed to loadLayer is a load-time snapshot, so we re-read live config.
 * After (re)mounting we poll fast until the PNG actually exists, then refresh slowly.
 *
 * globalKeys: extra top-level config sections (e.g. ['animation']) to read each
 * poll and pass through to mount/refresh as a second `globals` arg. They are
 * folded into the change signature, so editing a shared section live-updates
 * every layer that watches it. initialGlobals seeds the snapshot mount.
 */
export function liveLayerSync(map, {
    sectionKey, initialConfig, mount, refresh, unmount, imageUrl,
    syncMs = 20000, refreshMs = 300000,
    globalKeys = [], initialGlobals = {},
    regenWaitMs = 120000,
}) {
    let mounted = false;
    let imageReady = false;
    let lastRefresh = 0;
    let lastSig = '';                                 // JSON of last-seen section + globals
    // After a render-affecting config change the backend re-renders the PNG on its
    // own beat, so the new image lands a little later than we detect the config edit.
    // While "awaiting regen" we keep watching the image's Last-Modified and apply the
    // fresh render the moment it appears, instead of refreshing once and going dormant
    // for the slow cadence (which left stale renders on screen for minutes).
    let awaitingRegen = false;
    let regenDeadline = 0;
    let baselineMtime = 0;

    const pickGlobals = (data) => {
        const g = {};
        for (const k of globalKeys) g[k] = data ? data[k] : undefined;
        return g;
    };
    const sigOf = (section, globals) => JSON.stringify([section, globals]);

    const doMount = (cfg, globals) => {
        mount(cfg, globals); mounted = true; imageReady = false;
        lastRefresh = Date.now(); lastSig = sigOf(cfg, globals);
        awaitingRegen = false;
    };
    const doUnmount = () => {
        unmount(); mounted = false; imageReady = false; awaitingRegen = false;
    };

    if (initialConfig && initialConfig.enabled) doMount(initialConfig, initialGlobals);

    // Last-Modified of the served image as a millisecond timestamp (0 if unavailable).
    const imageMtime = async (cfg) => {
        if (!imageUrl) return 0;
        try {
            const r = await fetch(`${imageUrl(cfg)}?probe=${Date.now()}`, { method: 'HEAD' });
            if (!r.ok) return 0;
            const lm = r.headers.get('Last-Modified');
            const t = lm ? Date.parse(lm) : NaN;
            return Number.isNaN(t) ? 0 : t;
        } catch { return 0; }
    };

    const imageExists = async (cfg) => {
        if (!imageUrl) return true;                       // no probe supplied -> assume present
        try {
            const r = await fetch(`${imageUrl(cfg)}?probe=${Date.now()}`, { method: 'HEAD' });
            return r.ok;
        } catch { return false; }
    };

    const tick = async () => {
        let data;
        try {
            const res = await fetch(`${window.WM_API}/config?t=${Date.now()}`);
            data = (await res.json()).data || {};
        } catch (err) {
            console.warn(`[${sectionKey}] config check failed; leaving layer as-is`, err);
            return;
        }
        const section = data[sectionKey];
        const globals = pickGlobals(data);
        const enabled = !!(section && section.enabled);

        if (enabled && !mounted) {
            doMount(section, globals);
            console.log(`[${sectionKey}] enabled — mounting; awaiting image.`);
            return;                                       // readiness handled next tick
        }
        if (!enabled && mounted) {
            doUnmount();
            console.log(`[${sectionKey}] disabled — layer removed.`);
            return;
        }
        if (enabled && mounted) {
            if (!imageReady) {
                const exists = await imageExists(section);
                if (!mounted) return;          // disabled while the probe was in flight
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
                    // ...and start chasing the backend re-render so we don't sit on a
                    // stale PNG until the slow cadence.
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
                        // Last-Modified not exposed: re-pull each tick within the window
                        // so the new image is still picked up, just less precisely.
                        refresh(section, globals);
                        lastRefresh = Date.now();
                    }
                    if (Date.now() >= regenDeadline) awaitingRegen = false;
                } else if (Date.now() - lastRefresh >= refreshMs) {
                    refresh(section, globals);            // unchanged config: slow cadence
                    lastRefresh = Date.now();             // (picks up regenerated PNGs)
                }
            }
        }
    };

    return setInterval(tick, syncMs);
}