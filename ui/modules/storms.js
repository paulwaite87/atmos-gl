// ui/modules/storms.js

export async function loadLayer(map, config) {
    const sourceId = 'storms-source';

    if (map.getSource(sourceId)) return;

    const geojsonUrl = `${window.WM_API}/storms/geojson`;
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });

    try {
        console.log(`[Quakes] Fetching dataset from: ${geojsonUrl}`);
        const geoResponse = await fetch(`${geojsonUrl}?t=${Date.now()}`);
        if (!geoResponse.ok) throw new Error(`HTTP ${geoResponse.status}`);
        const geojsonData = await geoResponse.json();

        console.log(`🌀 [Storms] API returned ${geojsonData.features?.length || 0} geometric features.`);

        const bindToMap = () => {
            if (map.getSource(sourceId)) return;

            // Inject Unified Source
            map.addSource(sourceId, { type: 'geojson', data: geojsonData });

            // The Error Cone (Semi-transparent polygon)
            // Inside bindToMap()
            map.addLayer({
                id: 'storms-cone',
                type: 'fill',
                source: sourceId,
                filter: ['==', 'feature_type', 'CONE'],
                paint: {
                    'fill-color': '#ff4a4a',    // Change to match track red
                    'fill-opacity': 0.25,       // Bumped up from 0.15
                    'fill-outline-color': '#ff4a4a'
                }
            });

            // Past Track (Solid Red Line)
            map.addLayer({
                id: 'storms-track-past',
                type: 'line',
                source: sourceId,
                filter: ['==', 'feature_type', 'TRACK_PAST'],
                paint: {
                    'line-color': '#ff4a4a',
                    'line-width': 2
                }
            });

            // 4. Forecast Track (Dashed Red Line)
            map.addLayer({
                id: 'storms-track-forecast',
                type: 'line',
                source: sourceId,
                filter: ['==', 'feature_type', 'TRACK_FORECAST'],
                paint: {
                    'line-color': '#ff4a4a',
                    'line-width': 2,
                    'line-dasharray': [2, 2] // Creates the dashed effect
                }
            });

            // 5. Track Points (Interactive Dots)
            map.addLayer({
                id: 'storms-points',
                type: 'circle',
                source: sourceId,
                filter: ['==', 'feature_type', 'POINT'],
                paint: {
                    'circle-radius': [
                        'match',
                        ['get', 'record_type'],
                        'CURRENT', 6, // Make the current position larger
                        4             // Past/Forecast points are smaller
                    ],
                    'circle-color': '#111111',
                    'circle-stroke-color': '#ff4a4a',
                    'circle-stroke-width': 2
                }
            });

            // 6. Interactivity (Hover over points)
            map.on('mouseenter', 'storms-points', (e) => {
                map.getCanvas().style.cursor = 'pointer';
                const props = e.features[0].properties;
                const coords = e.features[0].geometry.coordinates.slice();

                // Format the timestamp nicely
                const dateStr = new Date(props.dt).toLocaleString(undefined, {
                    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                });

                popup.setLngLat(coords)
                    .setHTML(`
                        <div style="font-family: sans-serif; font-size: 12px; color: #000; padding: 4px;">
                            <strong style="color: #ff4a4a; font-size: 14px;">${props.name || props.sid}</strong>
                            <hr style="border: 0; border-top: 1px solid #ccc; margin: 4px 0;">
                            <div><span style="color: #666; width: 45px; display: inline-block;">Type:</span> <strong>${props.record_type}</strong></div>
                            <div><span style="color: #666; width: 45px; display: inline-block;">Time:</span> <strong>${dateStr}</strong></div>
                            ${props.tau > 0 ? `<div><span style="color: #666; width: 45px; display: inline-block;">Hour:</span> <strong>+${props.tau}</strong></div>` : ''}
                        </div>
                    `)
                    .addTo(map);
            });

            map.on('mouseleave', 'storms-points', () => {
                map.getCanvas().style.cursor = '';
                popup.remove();
            });

            // Add this helper inside loadLayer, before the closing brace of loadLayer
            function animatePulse() {
                if (!map.getSource(sourceId)) return; // Safety: stop if source is gone

                // Create an oscillating value between 0 and 1
                const pulse = (Math.sin(Date.now() / 400) + 1) / 2;

                // Scale: radius goes from 6 to 10
                const currentRadius = 6 + (pulse * 4);

                // Apply the animation ONLY to the 'CURRENT' record_type
                map.setPaintProperty('storms-points', 'circle-radius', [
                    'match',
                    ['get', 'record_type'],
                    'CURRENT', currentRadius, // Apply pulse here
                    4                         // Keep others small (size 4)
                ]);

                requestAnimationFrame(animatePulse);
            }

            // Start the animation loop
            animatePulse();
        };

        if (map.loaded()) {
            bindToMap();
        } else {
            map.once('load', bindToMap);
        }

    } catch (err) {
        console.error("❌ [Storms] Core initialization failure:", err);
    }
}