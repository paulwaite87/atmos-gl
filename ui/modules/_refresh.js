/**
 * Keep a raster image layer in live sync with its backend `enabled` flag.
 * The `config` handed to loadLayer is a load-time snapshot, so we re-read the
 * live config on a short interval and mount/unmount the layer accordingly.
 * Image bytes are refreshed on a slower cadence than the enable/disable check.
 */
export function liveLayerSync(map, {
    sectionKey, initialConfig, mount, refresh, unmount,
    syncMs = 20000, refreshMs = 300000,
}) {
    let mounted = false;
    let lastRefresh = 0;

    // Immediate mount from the snapshot so first paint isn't gated on a fetch
    if (initialConfig && initialConfig.enabled) {
        mount(initialConfig);
        mounted = true;
        lastRefresh = Date.now();
    }

    const tick = async () => {
        let section;
        try {
            const res = await fetch(`${window.WM_API}/config?t=${Date.now()}`);
            section = (await res.json()).data?.[sectionKey];
        } catch (err) {
            // Transient blip: leave the layer exactly as it is, try again next tick
            console.warn(`[${sectionKey}] config check failed; leaving layer as-is`, err);
            return;
        }

        const enabled = !!(section && section.enabled);

        if (enabled && !mounted) {
            mount(section);                 // re-attach with the latest settings
            mounted = true;
            lastRefresh = Date.now();
            console.log(`[${sectionKey}] enabled — layer mounted.`);
        } else if (!enabled && mounted) {
            unmount();
            mounted = false;
            console.log(`[${sectionKey}] disabled — layer removed.`);
        } else if (enabled && mounted && Date.now() - lastRefresh >= refreshMs) {
            refresh(section);
            lastRefresh = Date.now();
        }
    };

    return setInterval(tick, syncMs);
}