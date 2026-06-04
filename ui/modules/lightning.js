// ui/modules/lightning.js

export async function loadLayer(map, config) {
    const sourceId = 'lightning-source';
    const layerId = 'lightning-layer';

    // Default config fallbacks if not provided by backend
    const recentMins = config.strike_recent_minutes || 15;
    const keepMins = config.strike_keep_minutes || 60;
    const expiryHours = config.strike_expiry_hours || 2;

    const baseUrl = `${window.WM_API}/lightning/geojson`;
    const geojsonUrl = `${baseUrl}?expiry_hours=${expiryHours}`;

    if (map.getSource(sourceId)) return; // Prevent duplicates

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 15 });

    // 1. Load Images
    const boltIcons = [
        { id: 'bolt-white', url: '/images/bolt_white.png' },
        { id: 'bolt-yellow', url: '/images/bolt_yellow.png' },
        { id: 'bolt-red', url: '/images/bolt_red.png' }
    ];

    try {
        await Promise.all(boltIcons.map(async (icon) => {
            const response = await fetch(`${window.location.origin}${icon.url}`);
            if (!response.ok) throw new Error(`Could not load ${icon.id}`);
            const blob = await response.blob();
            const bitmap = await createImageBitmap(blob);
            if (!map.hasImage(icon.id)) map.addImage(icon.id, bitmap);
        }));

        // 2. Fetch Data
        const geoResponse = await fetch(`${geojsonUrl}&t=${Date.now()}`);
        const geojsonData = await geoResponse.json();

        map.addSource(sourceId, { type: 'geojson', data: geojsonData });

        // 3. Render WebGL Layer
        map.addLayer({
            id: layerId,
            type: 'symbol',
            source: sourceId,
            layout: {
                // DATA-DRIVEN STEP EXPRESSION:
                // Age < recentMins -> White
                // Age >= recentMins AND < keepMins -> Yellow
                // Age >= keepMins -> Red
                'icon-image': [
                    'step',
                    ['get', 'age_minutes'],
                    'bolt-white',
                    recentMins, 'bolt-yellow',
                    keepMins, 'bolt-red'
                ],
                'icon-size': 0.8,
                'icon-allow-overlap': true,
                'icon-ignore-placement': true
            }
        });

        console.log(`⚡ [Lightning] WebGL layer rendered with ${geojsonData.features.length} strikes.`);

    } catch (err) {
        console.error("❌ [Lightning] Error:", err);
    }

    // 4. Interaction Hooks
    map.on('mouseenter', layerId, (e) => {
        map.getCanvas().style.cursor = 'pointer';
        const feature = e.features[0].properties;
        const coordinates = e.features[0].geometry.coordinates.slice();

        const ageMins = Math.floor(feature.age_minutes);
        const ageDisplay = ageMins < 60 ? `${ageMins} mins ago` : `${(ageMins/60).toFixed(1)} hours ago`;
        const color = ageMins <= recentMins ? '#28a745' : (ageMins <= keepMins ? '#f0ad4e' : '#d9534f');

        popup.setLngLat(coordinates)
            .setHTML(`
                <div style="font-family: sans-serif; font-size: 12px; color: #000; padding: 5px;">
                    <strong style="color: #ff4a4a; font-size: 14px;">Strike at ${feature.timestamp}</strong> 
                    <hr style="border: 0; border-top: 1px solid #ccc; margin: 6px 0;">
                    <div><span style="color: #666; width: 40px; display: inline-block;">Age:</span> <strong style="color: ${color};">${ageDisplay}</strong></div>
                </div>
            `)
            .addTo(map);
    });

    map.on('mouseleave', layerId, () => {
        map.getCanvas().style.cursor = '';
        popup.remove();
    });
}