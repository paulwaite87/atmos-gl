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
 *
 * Every poll also re-checks GET /api/layer_availability (lib/layer_availability.py) --
 * a section's stored `enabled` is AND-ed with whether its data is actually being
 * collected right now, same signal config.html/me_settings.html gray their Show-tab
 * checkboxes with, so a layer stops rendering within one poll of an admin flipping the
 * relevant collector off, with no page reload needed. A section absent from
 * layer_availability has no collector dependency at all (e.g. terminator, landmass)
 * and is always treated as collecting.
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
            const [configRes, availRes] = await Promise.all([
                fetch(`${window.WM_API}/config?t=${Date.now()}`),
                fetch(`${window.WM_API}/layer_availability?t=${Date.now()}`),
            ]);
            const data = (await configRes.json()).data || {};
            const availability = (await availRes.json()).data || {};
            return { ok: true, data, availability };
        } catch (err) {
            console.warn(`[${sectionKey}] config check failed`, err);
            return { ok: false, data: null, availability: null };
        }
    };

    const withAvailability = (section, availability) => {
        if (!section) return section;
        const avail = availability && availability[sectionKey];
        if (!avail || avail.collecting) return section;
        return { ...section, enabled: false };
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
        const { ok, data, availability } = await fetchConfig();
        if (!ok) return;                                  // network blip: leave as-is
        await dispatch(withAvailability(data[sectionKey] || null, availability), data);
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
