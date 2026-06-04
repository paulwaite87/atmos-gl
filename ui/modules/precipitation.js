/**
 * Precipitation Module - Renders rainfall overlays with automatic background updates
 */
export function loadLayer(map, config) {
    const baseUrl = `http://localhost:9000`;
    const outfile = config.outfile;

    // 1. Robust path construction for the key image
    // This mirrors the backend: take base, add _key, append extension
    const lastDotIndex = outfile.lastIndexOf('.');
    const base = lastDotIndex !== -1 ? outfile.substring(0, lastDotIndex) : outfile;
    const ext = lastDotIndex !== -1 ? outfile.substring(lastDotIndex) : '';
    const keyUrl = `${baseUrl}/${base}_key${ext}`;

    const sourceId = 'precipitation-source';

    // 2. Establish the baseline layer source
    map.addSource(sourceId, {
        type: 'image',
        url: `${baseUrl}/${outfile}?t=${Date.now()}`,
        coordinates: [
            [-180, 85.051129],
            [180, 85.051129],
            [180, -85.051129],
            [-180, -85.051129]
        ]
    });

    map.addLayer({
        id: 'precipitation-layer',
        type: 'raster',
        source: sourceId,
        paint: {
            'raster-opacity': 0.85,
            'raster-fade-duration': 0
        }
    });

    // Inject the Legend Key into the sidebar
    const legendStack = document.getElementById('legend-stack');
    if (legendStack) {
        // Remove existing slot if we are re-loading
        const existingSlot = document.getElementById('precip-legend-slot');
        if (existingSlot) existingSlot.remove();

        const slot = document.createElement('div');
        slot.id = 'precip-legend-slot';
        slot.className = 'legend-slot';

        const keyImg = document.createElement('img');
        keyImg.src = `${keyUrl}?t=${Date.now()}`; // Add timestamp here too to ensure refresh
        keyImg.style.display = 'block';
        keyImg.style.width = '100%';

        slot.appendChild(keyImg);
        legendStack.appendChild(slot);
    }

    // 4. Refresh Timer
    setInterval(() => {
        const source = map.getSource(sourceId);
        if (source) {
            source.updateImage({
                url: `${baseUrl}/${outfile}?t=${Date.now()}`
            });
        }
    }, 300000);
}