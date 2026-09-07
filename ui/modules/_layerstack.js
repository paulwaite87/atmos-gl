// Shared "keep these layers pinned at the very top of the stack" helper --
// landmass.js (coastline outlines) and markers.js (place dots/labels) both need
// this so a fill layer toggled on/added later doesn't bury them (see landmass.js's
// own docstring for the two-listener asymmetry this participates in).
//
// MUST use map.getLayersOrder() here, not map.getStyle()?.layers: getStyle()
// returns the serializable style tree, which MapLibre OMITS every `type: 'custom'`
// layer from (every GPU fill layer in this app -- temperature/isobars/precipitation/
// waves/etc, see _webglfill.js's createFillLayer/createStaticFillLayer) -- so a
// getStyle()-based "am I on top?" check is blind to exactly the layers this
// exists to stay above, and silently no-ops instead of reclaiming the top. Found
// live: landmass's coastline stroke missing over the temperature layer specifically
// (a custom layer, unlike vector/symbol layers this check DOES see) -- confirmed
// via map.getLayersOrder() showing temperature-fill-layer above landmass-line even
// though map.getStyle().layers still reported landmass-line as the last entry.
// getLayersOrder() returns the true internal paint order, custom layers included.
export function keepLayersOnTop(map, layerIds, acceptableTopIds) {
    const order = map.getLayersOrder();
    if (!order.length) return;
    const topId = order[order.length - 1];
    if (acceptableTopIds.includes(topId)) return;
    for (const id of layerIds) {
        if (map.getLayer(id)) map.moveLayer(id);
    }
}
