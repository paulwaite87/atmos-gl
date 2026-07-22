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
 */
export function hoverPopup(map, layerId, { offset = 15, html, maxWidth }) {
    const popupOpts = { closeButton: false, closeOnClick: false, offset };
    if (maxWidth) popupOpts.maxWidth = maxWidth;
    const popup = new maplibregl.Popup(popupOpts);

    const onEnter = (e) => {
        if (!e.features.length) return;
        map.getCanvas().style.cursor = 'pointer';
        const coords = e.features[0].geometry.coordinates.slice();
        popup.setLngLat(coords).setHTML(html(e.features[0])).addTo(map);
    };
    const onLeave = () => { map.getCanvas().style.cursor = ''; popup.remove(); };

    map.on('mouseenter', layerId, onEnter);
    map.on('mouseleave', layerId, onLeave);

    return () => {
        map.off('mouseenter', layerId, onEnter);
        map.off('mouseleave', layerId, onLeave);
        popup.remove();
    };
}
