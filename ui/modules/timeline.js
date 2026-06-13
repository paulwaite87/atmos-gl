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
    maxHour: state.maxHour,
    runEpochUtc: state.runEpochUtc,
    validTimes: state.validTimes,
    refreshEpoch: state.refreshEpoch,
  };
}

function clampHour(h) {
  if (h < 0) return 0;
  if (h > state.maxHour) return state.maxHour;
  return h;
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
      state.hour = 0;            // loop back to 'now'
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
    state.maxHour = Math.max(0, Math.floor(n));
    if (state.hour > state.maxHour) state.hour = state.maxHour;
    emit();
  },

  // Configure speed + epoch metadata (called after fetching /api/forecast_state).
  configure({ maxHour, secondsPerHour, runEpochUtc, validTimes } = {}) {
    if (typeof maxHour === 'number') state.maxHour = Math.max(0, Math.floor(maxHour));
    if (typeof secondsPerHour === 'number' && secondsPerHour > 0) state.secondsPerHour = secondsPerHour;
    if (runEpochUtc !== undefined) state.runEpochUtc = runEpochUtc;
    if (validTimes !== undefined) state.validTimes = validTimes || {};
    if (state.hour > state.maxHour) state.hour = state.maxHour;
    emit();
  },

  // Called on a real data refresh: bump the cache-bust key so layers reload
  // textures for the (unchanged) current hour, and update epoch/labels. The
  // user's `hour` is intentionally preserved (hold forecast hour, swap data).
  onDataRefresh({ maxHour, runEpochUtc, validTimes } = {}) {
    state.refreshEpoch = Date.now();
    if (typeof maxHour === 'number') {
      state.maxHour = Math.max(0, Math.floor(maxHour));
      if (state.hour > state.maxHour) state.hour = state.maxHour;
    }
    if (runEpochUtc !== undefined) state.runEpochUtc = runEpochUtc;
    if (validTimes !== undefined) state.validTimes = validTimes || {};
    emit();
  },
};
