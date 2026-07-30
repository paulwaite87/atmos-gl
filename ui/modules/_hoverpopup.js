// ui/modules/_hoverpopup.js
/**
 * Shared hover-popup wiring behind quakes.js, storms.js, volcanoes.js, and
 * satellites.js -- architecture review candidate "a home for copy-pasted
 * legend/hover-popup plumbing". All four independently rebuilt the same
 * maplibregl.Popup construction, mouseenter/mouseleave cursor+setLngLat+setHTML+
 * addTo/remove dance, and map.on/off teardown. This owns that mechanics once; each
 * caller supplies only its own layerId and an html(feature) -> string renderer, since
 * the popup CONTENT is genuinely bespoke per layer (different fields, different
 * layout) and isn't part of the duplication.
 *
 * maxWidth is optional and omitted from the Popup options entirely when not given --
 * passing an explicit `undefined` through to `new maplibregl.Popup({..., maxWidth})`
 * would override MapLibre's own built-in default (240px) with `undefined` via
 * Object.assign's key-presence semantics, widening every caller's popup by accident.
 *
 * "Sticky" while hovered: the popup only closes once the mouse has left BOTH the
 * marker AND the popup's own DOM content, tracked as two independent booleans
 * (overMarker/overPopup) rather than a single flag -- otherwise leaving the marker
 * to move into the popup (Flight Radar's route/tooltip content is large enough to
 * need this) would still close it before the mouse ever reaches the popup. Every
 * caller gets this for free; simpler content (a one-line quake magnitude, say)
 * simply never has a reason to be entered, so the added listeners are inert there.
 *
 * closeDelayMs (default 200): leaving the marker doesn't remove the popup
 * immediately -- it's offset from the marker (see `offset`), so the cursor has to
 * cross a real gap of neither-hovered space to reach it, and an instant remove()
 * never gave it time to arrive. This is a grace period, not a fixed close delay:
 * it's cancelled the moment the mouse reaches the marker or the popup (see
 * cancelClose()), so a genuine move-away still closes promptly once the timer
 * fires. Long content needing to actually scroll (ui/index.html's
 * .maplibregl-popup-content max-height) made this gap-crossing failure visible,
 * but it applies to every caller uniformly, not just scrollable popups.
 */
export function hoverPopup(map, layerId, { offset = 15, html, maxWidth, closeDelayMs = 200 }) {
    const popupOpts = { closeButton: false, closeOnClick: false, offset };
    if (maxWidth) popupOpts.maxWidth = maxWidth;
    const popup = new maplibregl.Popup(popupOpts);

    let overMarker = false;
    let overPopup = false;
    let closeTimer = null;

    const cancelClose = () => {
        if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
    };

    const closeIfNeitherHovered = () => {
        cancelClose();
        if (overMarker || overPopup) return;
        closeTimer = setTimeout(() => {
            closeTimer = null;
            if (overMarker || overPopup) return;
            map.getCanvas().style.cursor = '';
            popup.remove();
        }, closeDelayMs);
    };

    const onPopupEnter = () => { overPopup = true; cancelClose(); };
    const onPopupLeave = () => { overPopup = false; closeIfNeitherHovered(); };

    const onEnter = (e) => {
        if (!e.features.length) return;
        overMarker = true;
        cancelClose();
        map.getCanvas().style.cursor = 'pointer';
        const coords = e.features[0].geometry.coordinates.slice();
        popup.setLngLat(coords).setHTML(html(e.features[0])).addTo(map);
        // Only reachable once addTo() has actually built the DOM -- re-wired on
        // every open since remove() discards the previous element.
        const el = popup.getElement();
        if (el) {
            el.addEventListener('mouseenter', onPopupEnter);
            el.addEventListener('mouseleave', onPopupLeave);
        }
    };
    const onLeave = () => { overMarker = false; closeIfNeitherHovered(); };

    map.on('mouseenter', layerId, onEnter);
    map.on('mouseleave', layerId, onLeave);

    return () => {
        cancelClose();
        map.off('mouseenter', layerId, onEnter);
        map.off('mouseleave', layerId, onLeave);
        const el = popup.getElement();
        if (el) {
            el.removeEventListener('mouseenter', onPopupEnter);
            el.removeEventListener('mouseleave', onPopupLeave);
        }
        popup.remove();
    };
}
