// Shared demand-driven backfill flagger.
//
// When a per-hour field PNG 404s (either the streak engine's image onerror, or the
// liveLayerSync HEAD probe in _refresh.js), the layer flags the missing
// (product, date, run, hour) to the backend, which fetches it and renders the PNG.
//
// Deduped per key across the whole session so one missing hour is flagged once — not
// every animation frame, every probe tick, or every viewer. A successful later load can
// clear its key (so a re-eviction re-flags) via clearFlag().

const flagged = new Set();

// Build the {key,date,run,hour} for a missing field. `resolve` may be:
//   - a function (snap) => {date,run,hour}   (layer-specific, e.g. currents/RTOFS), or
//   - null, in which case we derive date/run from snap.runEpochUtc (GFS run) + snap.hour.
function buildKey(sectionKey, snap, resolve) {
    if (resolve) {
        const k = resolve(snap);
        if (!k || !k.date || !k.run || k.hour == null) return null;
        return { key: `${sectionKey}:${k.date}:${k.run}:${k.hour}`,
                 date: k.date, run: k.run, hour: k.hour };
    }
    if (!snap || !snap.runEpochUtc) return null;
    const r = new Date(snap.runEpochUtc);
    if (isNaN(r.getTime())) return null;
    const date = `${r.getUTCFullYear()}${String(r.getUTCMonth() + 1).padStart(2, '0')}${String(r.getUTCDate()).padStart(2, '0')}`;
    const run = String(r.getUTCHours()).padStart(2, '0');
    return { key: `${sectionKey}:${date}:${run}:${snap.hour}`, date, run, hour: snap.hour };
}

export function flagBackfill(sectionKey, snap, resolve) {
    const m = buildKey(sectionKey, snap, resolve);
    if (!m || flagged.has(m.key)) return;            // no run identity yet, or already asked
    flagged.add(m.key);
    fetch(`${window.WM_API}/request_backfill`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product: sectionKey, date: m.date, run: m.run, hour: m.hour }),
    }).catch(() => { /* best-effort; the field stays transparent meanwhile */ });
}

export function clearBackfillFlag(sectionKey, snap, resolve) {
    const m = buildKey(sectionKey, snap, resolve);
    if (m) flagged.delete(m.key);
}
