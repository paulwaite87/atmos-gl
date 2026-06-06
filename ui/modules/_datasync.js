// ui/modules/_datasync.js
/**
 * Live-sync a GeoJSON (database-backed) layer with its backend `enabled` flag.
 * mount(section): async — fetch data, register icons, add source+layer(s), bind handlers.
 * refresh(section): async — re-fetch and setData only (no re-adding layers/handlers).
 * unmount(): remove handlers, layers, source, popups, stop any animation.
 */
export function liveDataSync(map, {
    sectionKey, initialConfig, mount, refresh, unmount,
    syncMs = 20000, refreshMs = 60000,
}) {
    let mounted = false, busy = false, lastRefresh = 0;

    const fetchSection = async () => {
        try {
            const res = await fetch(`${window.WM_API}/config?t=${Date.now()}`);
            return { ok: true, section: ((await res.json()).data || {})[sectionKey] || null };
        } catch (err) {
            console.warn(`[${sectionKey}] config check failed`, err);
            return { ok: false, section: null };
        }
    };

    const reconcile = async () => {
        if (busy) return;                              // serialize async mount/refresh
        const { ok, section } = await fetchSection();
        if (!ok) return;                               // network blip: leave as-is
        const enabled = !!(section && section.enabled);

        busy = true;
        try {
            if (enabled && !mounted) {
                await mount(section);
                const recheck = await fetchSection();  // disabled during the async mount?
                if (recheck.ok && !(recheck.section && recheck.section.enabled)) {
                    unmount();                         // yes — back it out immediately
                    return;
                }
                mounted = true; lastRefresh = Date.now();
                console.log(`[${sectionKey}] enabled — layer mounted.`);
            } else if (!enabled && mounted) {
                unmount(); mounted = false;
                console.log(`[${sectionKey}] disabled — layer removed.`);
            } else if (enabled && mounted && Date.now() - lastRefresh >= refreshMs) {
                await refresh(section); lastRefresh = Date.now();
            }
        } finally {
            busy = false;
        }
    };

    if (initialConfig && initialConfig.enabled) {      // fast first paint from snapshot
        busy = true;
        Promise.resolve(mount(initialConfig))
            .then(() => { mounted = true; lastRefresh = Date.now(); })
            .catch(err => console.error(`[${sectionKey}] initial mount failed`, err))
            .finally(() => { busy = false; });
    }
    return setInterval(reconcile, syncMs);
}