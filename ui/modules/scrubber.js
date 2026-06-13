// ui/modules/scrubber.js
//
// Bottom-center media-player widget controlling the shared forecast timeline:
//   [⏮]  [▶/⏸]  [⏭]      f+06h · Sat 18:00 UTC
//   <----------●--------------------->   (seek slider, 0..maxHour)
//
// Purely a view/controller over timeline.js — holds no animation state of its own.
// Mounts once; visible whenever at least one animated layer is active.

import { timeline } from './timeline.js';

const ID = 'scrubber-bar';
let styleInjected = false;
let els = null;        // cached DOM refs
let unsub = null;
let activeLayers = 0;  // ref count of animated layers currently mounted

const injectStyle = () => {
  if (styleInjected) return;
  styleInjected = true;
  const css = `
    #${ID} { position:fixed; left:50%; bottom:22px; transform:translateX(-50%);
        z-index:5; display:none; align-items:center; gap:12px;
        padding:8px 14px; border-radius:10px;
        background:rgba(20,24,33,.78); backdrop-filter:blur(8px);
        box-shadow:0 4px 18px rgba(0,0,0,.35);
        font:12px/1.2 system-ui,-apple-system,sans-serif; color:#fff;
        user-select:none; }
    #${ID}.visible { display:flex; }
    #${ID} .sc-btn { cursor:pointer; width:30px; height:30px; border:none; border-radius:7px;
        background:rgba(255,255,255,.10); color:#fff; font-size:14px; line-height:1;
        display:flex; align-items:center; justify-content:center; transition:background .12s; }
    #${ID} .sc-btn:hover { background:rgba(255,255,255,.22); }
    #${ID} .sc-btn:active { background:rgba(255,255,255,.30); }
    #${ID} .sc-play { width:34px; height:34px; font-size:16px;
        background:rgba(120,180,255,.30); }
    #${ID} .sc-play:hover { background:rgba(120,180,255,.45); }
    #${ID} .sc-slider { -webkit-appearance:none; appearance:none; width:220px; height:5px;
        border-radius:3px; background:rgba(255,255,255,.20); outline:none; cursor:pointer; }
    #${ID} .sc-slider::-webkit-slider-thumb { -webkit-appearance:none; appearance:none;
        width:14px; height:14px; border-radius:50%; background:#fff; cursor:pointer;
        box-shadow:0 0 3px rgba(0,0,0,.5); }
    #${ID} .sc-slider::-moz-range-thumb { width:14px; height:14px; border:none; border-radius:50%;
        background:#fff; cursor:pointer; box-shadow:0 0 3px rgba(0,0,0,.5); }
    #${ID} .sc-label { min-width:150px; text-align:left; font-variant-numeric:tabular-nums;
        white-space:nowrap; }
    #${ID} .sc-fhour { font-weight:700; }
    #${ID} .sc-valid { opacity:.7; margin-left:6px; }
  `;
  const s = document.createElement('style');
  s.textContent = css;
  document.head.appendChild(s);
};

// Offset is relative to the widget's start ('now' = minHour), NOT the underlying
// forecast hour. At the first position the offset is suppressed; thereafter +1h, +2h...
const fmtOffset = (snap) => {
  const rel = snap.hour - snap.minHour;
  if (rel <= 0) return '';                 // 'now' — no offset shown
  return `+${rel}h`;
};

const fmtValid = (snap) => {
  const iso = snap.validTimes && snap.validTimes[String(snap.hour)];
  if (!iso) return '';
  const d = new Date(iso);                  // ISO has 'Z' -> parsed as UTC instant
  if (isNaN(d)) return '';
  // Render in the browser's LOCAL timezone (no timeZone override => local).
  const day = d.toLocaleDateString(undefined, { weekday: 'short' });
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${day} ${hh}:${mm}`;
};

const ensureDom = () => {
  if (els) return els;
  injectStyle();
  const bar = document.createElement('div');
  bar.id = ID;
  bar.innerHTML = `
    <button class="sc-btn sc-step-back" title="Step back 1 hour">⏮</button>
    <button class="sc-btn sc-play" title="Play / pause">▶</button>
    <button class="sc-btn sc-step-fwd" title="Step forward 1 hour">⏭</button>
    <input class="sc-slider" type="range" min="0" max="23" step="1" value="0" title="Scrub forecast hour">
    <span class="sc-label"><span class="sc-fhour"></span><span class="sc-valid"></span></span>
  `;
  document.body.appendChild(bar);

  els = {
    bar,
    stepBack: bar.querySelector('.sc-step-back'),
    play: bar.querySelector('.sc-play'),
    stepFwd: bar.querySelector('.sc-step-fwd'),
    slider: bar.querySelector('.sc-slider'),
    fhour: bar.querySelector('.sc-fhour'),
    valid: bar.querySelector('.sc-valid'),
  };

  els.stepBack.addEventListener('click', () => timeline.stepBack());
  els.stepFwd.addEventListener('click', () => timeline.stepForward());
  els.play.addEventListener('click', () => timeline.toggle());
  // Dragging the slider pauses and seeks live.
  els.slider.addEventListener('input', (e) => timeline.seek(Number(e.target.value)));

  return els;
};

const render = (snap) => {
  if (!els) return;
  els.slider.min = String(snap.minHour);
  els.slider.max = String(snap.maxHour);
  // While playing, reflect the continuous position (hour+frac); when paused, the hour.
  const pos = snap.playing ? Math.min(snap.maxHour, snap.hour + snap.frac) : snap.hour;
  // Avoid yanking the thumb while the user drags: only set if not focused.
  if (document.activeElement !== els.slider) {
    els.slider.value = String(Math.round(pos));
  }
  els.play.textContent = snap.playing ? '⏸' : '▶';
  const off = fmtOffset(snap);
  const valid = fmtValid(snap);
  els.fhour.textContent = off;                          // '' at 'now', else '+Nh'
  els.valid.textContent = (off && valid) ? ' · ' + valid : valid;
};

const setVisible = (v) => {
  if (!els) return;
  els.bar.classList.toggle('visible', v);
};

export const scrubber = {
  // Mount the widget (idempotent). Subscribes to the timeline.
  mount() {
    ensureDom();
    if (!unsub) unsub = timeline.subscribe(render);
    render(timeline.get());
  },

  // Animated layers call these so the bar shows only when something animated is live.
  layerActivated() {
    activeLayers += 1;
    this.mount();
    setVisible(activeLayers > 0);
  },
  layerDeactivated() {
    activeLayers = Math.max(0, activeLayers - 1);
    setVisible(activeLayers > 0);
  },

  unmount() {
    if (unsub) { unsub(); unsub = null; }
    if (els && els.bar && els.bar.parentNode) els.bar.parentNode.removeChild(els.bar);
    els = null;
    activeLayers = 0;
  },
};