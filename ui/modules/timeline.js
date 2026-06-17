// ui/modules/timeline.js
//
// Shared animation clock for all hourly-animated layers (isobars, precipitation,
// wind, temperature, ozone, stormwatch). One authoritative timeline; layers and
// the scrubber widget subscribe to it.
//
// State the timeline owns:
//   hour      integer forecast hour, 0..maxHour (the discrete step the user is on)
//   frac      0..1 cross-fade position from `hour` toward `hour+1` (advances only
//             while playing; always 0 when paused/stepped)
//   playing   bool
//   maxHour   highest selectable hour (from /api/forecast_state fmax)
//
// Playback advances `frac` on a shared rAF loop; when frac reaches 1 it rolls to
// the next hour, wrapping to 0 after maxHour. secondsPerHour controls speed.
//
// Subscribers receive { hour, frac, playing, maxHour } on every state change and
// on every animation frame while playing.

const subscribers = new Set();

const state = {
  hour: 0,
  frac: 0,
  playing: false,
  minHour: 0,            // earliest available forecast hour (= /forecast_state fmin)
  maxHour: 23,
  secondsPerHour: 0.8,   // play speed; overridable via configure()
  runEpochUtc: null,     // ISO string, valid time of f000 (for labels)
  validTimes: {},        // { "0": ISO, ... } per-hour valid time
  refreshEpoch: Date.now(), // bumped on data refresh -> cache-bust key for layers
};

let rafId = null;
let lastTs = null;

function emit() {
  const snap = snapshot();
  for (const fn of subscribers) {
    try { fn(snap); } catch (e) { console.error('[timeline] subscriber error', e); }
  }
}

function snapshot() {
  return {
    hour: state.hour,
    frac: state.frac,
    playing: state.playing,
    minHour: state.minHour,
    maxHour: state.maxHour,
    runEpochUtc: state.runEpochUtc,
    validTimes: state.validTimes,
    refreshEpoch: state.refreshEpoch,
  };
}

function clampHour(h) {
  if (h < state.minHour) return state.minHour;
  if (h > state.maxHour) return state.maxHour;
  return h;
}

// The available forecast hour whose valid time is closest to the user's current wall
// clock — i.e. real 'now'. Uses the per-hour validTimes (absolute UTC instants), so it
// is timezone-correct regardless of the viewer's locale. Falls back to minHour when
// valid times aren't loaded yet (e.g. before the first forecast_state fetch).
function nowHour() {
  const vt = state.validTimes;
  if (!vt || !Object.keys(vt).length) return state.minHour;
  const now = Date.now();
  let best = state.minHour, bestDiff = Infinity;
  for (let h = state.minHour; h <= state.maxHour; h++) {
    const iso = vt[String(h)];
    if (!iso) continue;
    const diff = Math.abs(new Date(iso).getTime() - now);
    if (diff < bestDiff) { bestDiff = diff; best = h; }
  }
  return best;
}

function tick(ts) {
  if (!state.playing) { rafId = null; lastTs = null; return; }
  if (lastTs == null) lastTs = ts;
  const dt = (ts - lastTs) / 1000;   // seconds since last frame
  lastTs = ts;

  const perHour = Math.max(0.05, state.secondsPerHour);
  state.frac += dt / perHour;

  while (state.frac >= 1) {
    state.frac -= 1;
    if (state.hour >= state.maxHour) {
      state.hour = state.minHour;   // loop back to the earliest available hour ('now')
    } else {
      state.hour += 1;
    }
  }
  emit();
  rafId = requestAnimationFrame(tick);
}

function startLoop() {
  if (rafId == null) {
    lastTs = null;
    rafId = requestAnimationFrame(tick);
  }
}

function stopLoop() {
  if (rafId != null) cancelAnimationFrame(rafId);
  rafId = null;
  lastTs = null;
}

export const timeline = {
  get: snapshot,

  subscribe(fn) {
    subscribers.add(fn);
    // Immediately give the new subscriber the current state.
    try { fn(snapshot()); } catch (e) { /* ignore */ }
    return () => subscribers.delete(fn);
  },
  unsubscribe(fn) { subscribers.delete(fn); },

  play() {
    if (state.playing) return;
    state.playing = true;
    startLoop();
    emit();
  },
  pause() {
    if (!state.playing) return;
    state.playing = false;
    state.frac = 0;           // settle exactly on the current hour
    stopLoop();
    emit();
  },
  toggle() { state.playing ? this.pause() : this.play(); },

  stepForward() {
    this.pause();
    state.hour = clampHour(state.hour + 1);
    state.frac = 0;
    emit();
  },
  stepBack() {
    this.pause();
    state.hour = clampHour(state.hour - 1);
    state.frac = 0;
    emit();
  },
  seek(hour) {
    this.pause();
    state.hour = clampHour(Math.round(hour));
    state.frac = 0;
    emit();
  },

  setMaxHour(n) {
    state.maxHour = Math.max(state.minHour, Math.floor(n));
    if (state.hour > state.maxHour) state.hour = state.maxHour;
    emit();
  },

  // Update playback speed ONLY, without emitting. The play loop reads
  // state.secondsPerHour live each tick, so no subscriber needs notifying — and
  // emitting here would make animated layers treat it as a data/hour change and
  // visibly blink (reload/redraw). Used by the live config re-read of stepping rate.
  setSecondsPerHour(n) {
    if (typeof n === 'number' && n > 0) state.secondsPerHour = n;
  },

  // Configure range + speed + epoch metadata (called after fetching /api/forecast_state).
  // `initialise` true on first config: snap the current hour to minHour ('now').
  configure({ minHour, maxHour, secondsPerHour, runEpochUtc, validTimes, initialise } = {}) {
    if (typeof minHour === 'number') state.minHour = Math.floor(minHour);
    if (typeof maxHour === 'number') state.maxHour = Math.max(state.minHour, Math.floor(maxHour));
    if (typeof secondsPerHour === 'number' && secondsPerHour > 0) state.secondsPerHour = secondsPerHour;
    if (runEpochUtc !== undefined) state.runEpochUtc = runEpochUtc;
    if (validTimes !== undefined) state.validTimes = validTimes || {};
    // On first configuration, start at the user's actual 'now' — the available hour
    // whose valid time is closest to the wall clock. (Don't assume minHour == now:
    // the earliest CATALOGUED hour can lag real time by hours, due to model-run
    // publish latency and the ingest window, which would otherwise open the scrubber
    // in the past.) Falls back to minHour if valid times aren't available yet.
    if (initialise) state.hour = nowHour();
    if (state.hour < state.minHour) state.hour = state.minHour;
    if (state.hour > state.maxHour) state.hour = state.maxHour;
    emit();
  },

  // Called on a real data refresh: bump the cache-bust key so layers reload
  // textures for the (held) current hour, and update epoch/labels/range. The
  // user's `hour` is preserved where possible (hold forecast hour, swap data),
  // but re-clamped into the new [minHour, maxHour] window.
  onDataRefresh({ minHour, maxHour, runEpochUtc, validTimes } = {}) {
    state.refreshEpoch = Date.now();
    if (typeof minHour === 'number') state.minHour = Math.floor(minHour);
    if (typeof maxHour === 'number') state.maxHour = Math.max(state.minHour, Math.floor(maxHour));
    if (state.hour < state.minHour) state.hour = state.minHour;
    if (state.hour > state.maxHour) state.hour = state.maxHour;
    if (runEpochUtc !== undefined) state.runEpochUtc = runEpochUtc;
    if (validTimes !== undefined) state.validTimes = validTimes || {};
    emit();
  },
};