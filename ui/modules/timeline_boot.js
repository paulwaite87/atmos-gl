// ui/modules/timeline_boot.js
//
// Boots the shared forecast timeline from the backend and keeps it live:
//   - fetches /api/forecast_state for the run epoch + available hours
//   - configures timeline (maxHour, play speed, valid-time labels)
//   - polls periodically; on a new run or changed hour range, calls
//     timeline.onDataRefresh() so layers bust their texture caches and reload
//     the (held) current hour against the fresher data.
//
// Call initForecastTimeline(configData) once, after window.WM_API is set and the
// map style has loaded. Safe to call before any animated layer mounts — layers
// subscribe to the timeline independently.

import { timeline } from './timeline.js';
import { scrubber } from './scrubber.js';

const POLL_MS = 60000;   // re-check forecast state every 60s
const STEP_POLL_MS = 20000;   // re-check stepping speed from config every 20s (matches layer sync)

let pollId = null;
let stepPollId = null;
let lastSig = '';
let lastSecondsPerHour = null;   // last stepping speed applied to the timeline

function secondsPerHourFrom(configData) {
    // Derive number of seconds per step (hour) in the forecast animation.
    // Values from the UI can be in the range 0-100. We want to translate
    // that to values such that 100 represents about 0.1 seconds per step
    // and zero is about 2 seconds per step.
    const anim = (configData && configData.animation) || {};
    if (anim) {
        const srate = Math.max(100 - anim.stepping_rate, 5)
        const hs = Number(srate / 50);
        if (isFinite(hs) && hs > 0) return hs;
    }
    return 0.8;   // default ~0.8s per forecast hour during play
}

async function fetchState() {
    try {
        const res = await fetch(`${window.WM_API}/forecast_state?t=${Date.now()}`);
        if (!res.ok) return null;
        const json = await res.json();
        return json && json.status === 'success' ? json.data : null;
    } catch (e) {
        console.warn('[timeline_boot] forecast_state fetch failed', e);
        return null;
    }
}

function signatureOf(data) {
    if (!data) return 'none';
    return `${data.gfs_date}|${data.gfs_run}|${data.fmin}|${data.fmax}`;
}

export async function initForecastTimeline(configData) {
    const secondsPerHour = secondsPerHourFrom(configData);
    lastSecondsPerHour = secondsPerHour;

    const data = await fetchState();
    if (data) {
        timeline.configure({
            minHour: data.fmin,
            maxHour: data.max_hour,
            secondsPerHour,
            runEpochUtc: data.run_epoch_utc,
            validTimes: data.valid_times_utc,
            initialise: true,        // start at 'now' = earliest available hour
        });
        lastSig = signatureOf(data);
    } else {
        // No data yet (collector still warming up). Leave defaults; the poll will
        // pick it up once the catalog has hours.
        timeline.configure({ secondsPerHour });
    }

    // Mount the widget (it self-hides until a layer activates it).
    scrubber.mount();

    // Poll for run/hour-range changes.
    if (pollId) clearInterval(pollId);
    pollId = setInterval(async () => {
        const d = await fetchState();
        if (!d) return;
        const sig = signatureOf(d);
        if (sig !== lastSig) {
            lastSig = sig;
            // New run or changed availability: hold the user's forecast hour, but
            // bust caches + update epoch/labels so layers reload fresher data.
            timeline.onDataRefresh({
                minHour: d.fmin,
                maxHour: d.max_hour,
                runEpochUtc: d.run_epoch_utc,
                validTimes: d.valid_times_utc,
            });
        }
    }, POLL_MS);

    // Re-read the stepping speed from config on a fast cadence so changes made in the
    // config UI take effect without a hard-refresh (the forecast-state poll above is too
    // slow at 60s, and only watches run/hour changes — not the animation block). Fetches
    // /config, recomputes secondsPerHour, and reconfigures the timeline only on change.
    if (stepPollId) clearInterval(stepPollId);
    stepPollId = setInterval(async () => {
        let data;
        try {
            const res = await fetch(`${window.WM_API}/config?t=${Date.now()}`);
            if (!res.ok) return;
            data = (await res.json()).data || {};
        } catch (e) {
            return;   // transient; try again next tick
        }
        const sph = secondsPerHourFrom(data);
        if (sph !== lastSecondsPerHour) {
            lastSecondsPerHour = sph;
            timeline.configure({ secondsPerHour: sph });   // hold hour/range; just update speed
        }
    }, STEP_POLL_MS);
}

export function stopForecastTimeline() {
    if (pollId) { clearInterval(pollId); pollId = null; }
    if (stepPollId) { clearInterval(stepPollId); stepPollId = null; }
}
