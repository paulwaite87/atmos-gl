/**
 * Isobars Module - Renders flat pressure contour overlays with automatic background updates
 */
export function loadLayer(map, config) {
    const baseUrl = `${window.MAP_UI}`;
    const imageUrl = `${baseUrl}/${config.outfile}`;
    const sourceId = 'isobars-source';

    // 1. Establish the baseline layer source on startup
    map.addSource(sourceId, {
        type: 'image',
        url: `${imageUrl}?t=${Date.now()}`, // Cache-bust the very first load
        coordinates: [
            [-180, 85.051129],
            [180, 85.051129],
            [180, -85.051129],
            [-180, -85.051129]
        ]
    });

    map.addLayer({
        id: 'isobars-layer',
        type: 'raster',
        source: sourceId,
        paint: {
            'raster-opacity': 0.85,
            'raster-fade-duration': 0
        }
    });

    // Set up a background heartbeat timer
    // Check your backend for an updated file every 5 minutes (300,000 milliseconds)
    setInterval(() => {
        console.log("[Refresh] Requesting latest isobar texture map from backend...");

        const source = map.getSource(sourceId);
        if (source) {
            source.updateImage({
                url: `${imageUrl}?t=${Date.now()}`
            });
        }
    }, 300000);
}
