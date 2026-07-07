// ui/modules/_reconcile.js
/**
 * Shared poll-and-reconcile skeleton behind liveDataSync (_datasync.js) and
 * liveLayerSync (_refresh.js) -- architecture review candidate "unify the two
 * reconcile engines". Both used to independently re-derive the same "poll /config,
 * mount when enabled, refresh on change, unmount when disabled" loop, with different
 * (and in liveLayerSync's case, incomplete) concurrency guards -- a fix to one
 * wouldn't reach the other. This owns that loop once; each caller supplies only the
 * behavior specific to its layer kind via three hooks.
 *
 * Deliberately does NOT know about globals/globalKeys (a liveLayerSync-only concept)
 * -- hooks receive the raw fetched config blob and pick out whatever extra keys they
 * need themselves, so this stays honestly just "poll + dispatch + guard".
 */
export function reconcileLoop(map, {
    sectionKey, initialConfig, syncMs = 20000,
    onEnable,   // async (section, data) => boolean -- did it end up mounted?
    onDisable,  // () => void
    onTick,     // async (section, data) => void -- called while enabled && mounted
}) {
    let mounted = false, busy = false;

    const fetchConfig = async () => {
        try {
            const res = await fetch(`${window.WM_API}/config?t=${Date.now()}`);
            return { ok: true, data: (await res.json()).data || {} };
        } catch (err) {
            console.warn(`[${sectionKey}] config check failed`, err);
            return { ok: false, data: null };
        }
    };

    const dispatch = async (section, data) => {
        if (busy) return;                                // serialize async mount/refresh
        busy = true;
        try {
            const enabled = !!(section && section.enabled);
            if (enabled && !mounted) {
                mounted = await onEnable(section, data);
            } else if (!enabled && mounted) {
                onDisable();
                mounted = false;
            } else if (enabled && mounted) {
                await onTick(section, data);
            }
        } finally {
            busy = false;
        }
    };

    const tick = async () => {
        const { ok, data } = await fetchConfig();
        if (!ok) return;                                  // network blip: leave as-is
        await dispatch(data[sectionKey] || null, data);
    };

    // Fast first paint from snapshot: routes through the SAME busy-locked onEnable
    // path as an interval-triggered enable-transition, so it gets the same
    // mount-then-recheck-style safety any caller's onEnable implements.
    if (initialConfig) {
        dispatch(initialConfig, { [sectionKey]: initialConfig }).catch(err =>
            console.error(`[${sectionKey}] initial mount failed`, err)
        );
    }

    const intervalId = setInterval(tick, syncMs);

    // Teardown: stop the reconcile interval and unmount if currently mounted.
    // Returned so the host can clean up this layer before a basemap style swap
    // (setStyle wipes layers/sources) without leaking the interval or handlers.
    return () => {
        clearInterval(intervalId);
        if (mounted) { try { onDisable(); } catch (e) { console.warn(`[${sectionKey}] unmount failed`, e); } mounted = false; }
    };
}
