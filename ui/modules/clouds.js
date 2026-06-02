/**
 * Clouds Module - Projects global cloud cover textures onto the sphere
 */
export function loadLayer(map, config) {
    const baseUrl = `http://localhost:9000/${config.outfile}`;
    const sourceId = 'clouds-source';

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
        id: 'clouds-layer',
        type: 'raster',
        source: sourceId,
        paint: {
            'raster-opacity': 0.85,
            'raster-fade-duration': 0
        }
    });

    // Check your backend for an updated file every 5 minutes (300,000 milliseconds)
    setInterval(() => {
        console.log("[Refresh] Requesting latest clouds texture map from backend...");
        
        const source = map.getSource(sourceId);
        if (source) {
            source.updateImage({
                url: `${baseUrl}?t=${Date.now()}`
            });
        }
    }, 300000); 
}
