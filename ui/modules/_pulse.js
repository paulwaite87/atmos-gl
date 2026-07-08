// ui/modules/_pulse.js
/**
 * Shared "pulsing dot" RAF loop behind storms.js and satellites.js -- architecture
 * review candidate "a home for copy-pasted legend/hover-popup plumbing". Both
 * independently rebuilt the same start/stop-guarded requestAnimationFrame loop
 * sine-oscillating a circle-radius paint property. Only the shape of the paint value
 * differs (satellites sets a raw number; storms wraps it in a match() expression keyed
 * on record_type), so that's the one thing left to the caller via `toValue`.
 */
export function startPulse(map, layerId, property, { base, amp = 4, periodMs = 400, toValue = (r) => r }) {
    let running = true;
    const loop = () => {
        if (!running || !map.getLayer(layerId)) return;
        const r = base + ((Math.sin(Date.now() / periodMs) + 1) / 2) * amp;
        map.setPaintProperty(layerId, property, toValue(r));
        requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    return () => { running = false; };
}
