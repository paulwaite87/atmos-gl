// ui/modules/_datasync.js
/**
 * Live-sync a GeoJSON (database-backed) layer with its backend `enabled` flag.
 * mount(section): async — fetch data, register icons, add source+layer(s), bind handlers.
 * refresh(section): async — re-fetch and setData only (no re-adding layers/handlers).
 * unmount(): remove handlers, layers, source, popups, stop any animation.
 *
 * The poll loop, enabled/disabled dispatch, busy-lock, and teardown are owned by the
 * shared reconcileLoop (_reconcile.js, architecture review candidate "unify the two
 * reconcile engines"); this module supplies only what's specific to GeoJSON layers --
 * the mount-then-recheck-then-maybe-back-out sequence and the signature/cadence
 * refresh policy.
 */
import { reconcileLoop } from './_reconcile.js';

export function liveDataSync(map, {
    sectionKey, initialConfig, mount, refresh, unmount,
    syncMs = 20000, refreshMs = 60000,
}) {
    let lastRefresh = 0, lastSig = '';

    const fetchSection = async () => {
        try {
            const res = await fetch(`${window.WM_API}/config?t=${Date.now()}`);
            return { ok: true, section: ((await res.json()).data || {})[sectionKey] || null };
        } catch (err) {
            console.warn(`[${sectionKey}] config check failed`, err);
            return { ok: false, section: null };
        }
    };

    const onEnable = async (section) => {
        await mount(section);
        const recheck = await fetchSection();  // disabled during the async mount?
        if (recheck.ok && !(recheck.section && recheck.section.enabled)) {
            unmount();                         // yes — back it out immediately
            return false;
        }
        lastRefresh = Date.now(); lastSig = JSON.stringify(section);
        console.log(`[${sectionKey}] enabled — layer mounted.`);
        return true;
    };

    const onDisable = () => {
        unmount();
        console.log(`[${sectionKey}] disabled — layer removed.`);
    };

    const onTick = async (section) => {
        const sig = JSON.stringify(section);
        if (sig !== lastSig) {                     // settings changed -> apply now
            await refresh(section); lastSig = sig; lastRefresh = Date.now();
        } else if (Date.now() - lastRefresh >= refreshMs) {
            await refresh(section); lastRefresh = Date.now();   // slow cadence (fresh data)
        }
    };

    return reconcileLoop(map, { sectionKey, initialConfig, syncMs, onEnable, onDisable, onTick });
}
