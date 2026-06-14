/**
 * Shared forecast progress bar for the animated raster layers (isobars, precipitation).
 *
 * All animated layers share one [animation] timeline and one clock (driven from
 * the shared timeline in timeline.js), so a single bar represents them all. Layers
 * register via forecastHud.set(key, params) when they enter animated mode and
 * forecastHud.clear(key) when they leave. The bar lives in the #legend-stack key-slot
 * container and a marker tracks the animation using the same phase formula the layers
 * use, so it stays exactly in step.
 *
 * params: { loopMs, bounce, startHour, stepHours, frames }
 *   startHour = common.forecast_hour (hours ahead of "now" for frame 0)
 *   the span runs startHour .. startHour + (frames-1)*stepHours
 */

const SLOT_ID = 'forecast-progress-slot';
const layers = new Map();          // key -> params
let rafId = null;
let lastSig = '';
let styleInjected = false;

const injectStyle = () => {
    if (styleInjected) return;
    styleInjected = true;
    const css = `
      #${SLOT_ID} { font: 11px/1.3 system-ui, -apple-system, sans-serif; color:#fff;
            padding:6px 4px 4px; min-width:180px; }
      #${SLOT_ID} .fc-head { display:flex; justify-content:space-between; align-items:baseline;
            margin-bottom:5px; }
      #${SLOT_ID} .fc-title { opacity:.7; text-transform:uppercase; letter-spacing:.04em;
            font-size:9px; }
      #${SLOT_ID} .fc-now { font-weight:700; font-variant-numeric:tabular-nums; }
      #${SLOT_ID} .fc-track { position:relative; height:6px; border-radius:3px;
            background:rgba(255,255,255,.18); }
      #${SLOT_ID} .fc-fill { position:absolute; left:0; top:0; bottom:0; width:0;
            border-radius:3px; background:rgba(120,180,255,.6); }
      #${SLOT_ID} .fc-tick { position:absolute; top:-2px; width:1px; height:10px;
            background:rgba(255,255,255,.4); transform:translateX(-.5px); }
      #${SLOT_ID} .fc-marker { position:absolute; top:-4px; width:0; height:0;
            border-left:5px solid transparent; border-right:5px solid transparent;
            border-top:8px solid #fff; transform:translateX(-5px);
            filter:drop-shadow(0 0 1px rgba(0,0,0,.6)); }
      #${SLOT_ID} .fc-labels { position:relative; height:12px; margin-top:3px; }
      #${SLOT_ID} .fc-lab { position:absolute; transform:translateX(-50%); opacity:.6;
            font-size:9px; white-space:nowrap; font-variant-numeric:tabular-nums; }
      #${SLOT_ID} .fc-lab.edge-l { transform:translateX(0); }
      #${SLOT_ID} .fc-lab.edge-r { transform:translateX(-100%); }
    `;
    const s = document.createElement('style');
    s.textContent = css;
    document.head.appendChild(s);
};

const ensureDom = () => {
    const stack = document.getElementById('legend-stack');
    if (!stack) return null;
    let slot = document.getElementById(SLOT_ID);
    if (!slot) {
        injectStyle();
        slot = document.createElement('div');
        slot.id = SLOT_ID;
        slot.className = 'legend-slot';
        slot.innerHTML =
            '<div class="fc-head"><span class="fc-title">Forecast</span><span class="fc-now"></span></div>' +
            '<div class="fc-track"><div class="fc-fill"></div><div class="fc-marker"></div></div>' +
            '<div class="fc-labels"></div>';
        stack.insertBefore(slot, stack.firstChild);   // timeline sits at the top of the stack
    }
    return slot;
};

const phaseFor = (loopMs, bounce, now) => {
    if (bounce) {
        const p = (now % (2 * loopMs)) / loopMs;       // 0..2
        return p <= 1 ? p : 2 - p;                     // matches the layer loop phase
    }
    return (now % loopMs) / loopMs;                    // forward
};

// Rebuild ticks + hour labels only when the hours/frames actually change.
const renderScale = (slot, p) => {
    const sig = `${p.startHour}|${p.stepHours}|${p.frames}`;
    if (sig === lastSig) return;
    lastSig = sig;
    const track = slot.querySelector('.fc-track');
    track.querySelectorAll('.fc-tick').forEach((e) => e.remove());
    const labels = slot.querySelector('.fc-labels');
    labels.innerHTML = '';
    const N = Math.max(2, p.frames);
    const span = N - 1;
    const labelEvery = N > 6 ? 2 : 1;                  // thin labels when crowded
    for (let k = 0; k < N; k++) {
        const pct = (k / span) * 100;
        const tick = document.createElement('div');
        tick.className = 'fc-tick';
        tick.style.left = pct + '%';
        track.appendChild(tick);
        if (k % labelEvery === 0 || k === N - 1) {
            const lab = document.createElement('div');
            lab.className = 'fc-lab' + (k === 0 ? ' edge-l' : k === N - 1 ? ' edge-r' : '');
            lab.style.left = pct + '%';
            lab.textContent = '+' + (p.startHour + k * p.stepHours) + 'h';
            labels.appendChild(lab);
        }
    }
};

const frame = () => {
    const slot = ensureDom();
    if (!slot || layers.size === 0) { stop(); return; }
    const p = [...layers.values()][layers.size - 1];   // shared timeline; any entry works
    if (!p.loopMs || !p.frames) { rafId = requestAnimationFrame(frame); return; }
    renderScale(slot, p);
    const t = phaseFor(p.loopMs, p.bounce, performance.now());
    const pct = t * 100;
    slot.querySelector('.fc-marker').style.left = pct + '%';
    slot.querySelector('.fc-fill').style.width = pct + '%';
    const curHour = p.startHour + t * (p.frames - 1) * p.stepHours;
    slot.querySelector('.fc-now').textContent = '+' + Math.round(curHour) + 'h';
    rafId = requestAnimationFrame(frame);
};

const start = () => { if (rafId == null) rafId = requestAnimationFrame(frame); };
function stop() {
    if (rafId != null) { cancelAnimationFrame(rafId); rafId = null; }
    document.getElementById(SLOT_ID)?.remove();
    lastSig = '';
}

export const forecastHud = {
    set(key, params) { layers.set(key, params); start(); },
    clear(key) { layers.delete(key); if (layers.size === 0) stop(); },
};