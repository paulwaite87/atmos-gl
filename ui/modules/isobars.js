/**
 * Isobars Module - Renders flat pressure contour overlays with automatic background updates
 */
export function loadLayer(map, config) {
    const baseUrl = `http://localhost:9000/${config.outfile}`;
    const sourceId = 'isobars-source';

    // 1. Establish the baseline layer source on startup
    map.addSource(sourceId, {
        type: 'image',
        url: `${baseUrl}?t=${Date.now()}`, // Cache-bust the very first load
        coordinates: [
            [-180, 90],
            [180, 90],
            [180, -90],
            [-180, -90]
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

    // 2. WHERE TO USE IT: Set up a background heartbeat timer
    // Check your backend for an updated file every 5 minutes (300,000 milliseconds)
    setInterval(() => {
        console.log("[Refresh] Requesting latest isobar texture map from backend...");

        const source = map.getSource(sourceId);
        if (source) {
            source.updateImage({
                url: `${baseUrl}?t=${Date.now()}`
            });
        }
    }, 300000);
}
