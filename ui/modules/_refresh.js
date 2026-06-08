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
}) {
    let mounted = false;
    let imageReady = false;
    let lastRefresh = 0;
    let lastSig = '';                                 // JSON of last-seen section + globals

    const pickGlobals = (data) => {
        const g = {};
        for (const k of globalKeys) g[k] = data ? data[k] : undefined;
        return g;
    };
    const sigOf = (section, globals) => JSON.stringify([section, globals]);

    const doMount = (cfg, globals) => {
        mount(cfg, globals); mounted = true; imageReady = false;
        lastRefresh = Date.now(); lastSig = sigOf(cfg, globals);
    };
    const doUnmount = () => { unmount(); mounted = false; imageReady = false; };

    if (initialConfig && initialConfig.enabled) doMount(initialConfig, initialGlobals);

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
                if (sig !== lastSig) {                    // settings changed -> apply now
                    refresh(section, globals);
                    lastSig = sig;
                    lastRefresh = Date.now();
                } else if (Date.now() - lastRefresh >= refreshMs) {
                    refresh(section, globals);            // unchanged config: slow cadence
                    lastRefresh = Date.now();             // (picks up regenerated PNGs)
                }
            }
        }
    };

    return setInterval(tick, syncMs);
}