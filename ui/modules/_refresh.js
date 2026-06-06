/**
 * Keep a raster image layer in live sync with its backend `enabled` flag.
 * config handed to loadLayer is a load-time snapshot, so we re-read live config.
 * After (re)mounting we poll fast until the PNG actually exists, then refresh slowly.
 */
export function liveLayerSync(map, {
    sectionKey, initialConfig, mount, refresh, unmount, imageUrl,
    syncMs = 20000, refreshMs = 300000,
}) {
    let mounted = false;
    let imageReady = false;
    let lastRefresh = 0;

    const doMount = (cfg) => { mount(cfg); mounted = true; imageReady = false; lastRefresh = Date.now(); };
    const doUnmount = () => { unmount(); mounted = false; imageReady = false; };

    if (initialConfig && initialConfig.enabled) doMount(initialConfig);

    const imageExists = async (cfg) => {
        if (!imageUrl) return true;                       // no probe supplied -> assume present
        try {
            const r = await fetch(`${imageUrl(cfg)}?probe=${Date.now()}`, { method: 'HEAD' });
            return r.ok;
        } catch { return false; }
    };

    const tick = async () => {
        let section;
        try {
            const res = await fetch(`${window.WM_API}/config?t=${Date.now()}`);
            section = (await res.json()).data?.[sectionKey];
        } catch (err) {
            console.warn(`[${sectionKey}] config check failed; leaving layer as-is`, err);
            return;
        }
        const enabled = !!(section && section.enabled);

        if (enabled && !mounted) {
            doMount(section);
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
                    refresh(section);
                    imageReady = true;
                    lastRefresh = Date.now();
                    console.log(`[${sectionKey}] image ready — layer shown.`);
                }
            } else if (Date.now() - lastRefresh >= refreshMs) {
                refresh(section);                         // normal slow cadence
                lastRefresh = Date.now();
            }
        }
    };

    return setInterval(tick, syncMs);
}