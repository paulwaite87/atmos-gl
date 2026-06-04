// ui/modules/shipping.js

export async function loadLayer(map, config) {
    const geojsonUrl = 'http://localhost:9000/api/ships/geojson';
    if (map.getSource('ships-source')) return;

    // Single persistent popup instance
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });

    // 1. Define and Load all 3 Images
    const shipIcons = [
        { id: 'ship-red', url: '/images/red_ship_base.png' },
        { id: 'ship-green', url: '/images/green_ship_base.png' },
        { id: 'ship-purple', url: '/images/purple_ship_base.png' }
    ];

    try {
        await Promise.all(shipIcons.map(async (icon) => {
            const response = await fetch(`${window.location.origin}${icon.url}`);
            if (!response.ok) throw new Error(`Could not load ${icon.id}`);
            const blob = await response.blob();
            const bitmap = await createImageBitmap(blob);
            map.addImage(icon.id, bitmap);
        }));

        // 2. Fetch GeoJSON
        const geoResponse = await fetch(geojsonUrl);
        const geojsonData = await geoResponse.json();

        map.addSource('ships-source', { type: 'geojson', data: geojsonData, tolerance: 0.5 });

        // 3. Render Symbols with color-coded match expression
        map.addLayer({
            id: 'ships-layer',
            type: 'symbol',
            source: 'ships-source',
            minzoom: 3,
            // DYNAMIC FILTER:
            // Zooms < 4: Only ships >= 200m
            // Zooms 4-7: Only ships >= 100m
            // Zooms > 7: All ships
            filter: [
                'all',
                ['>=', ['get', 'length'],
                    ['step', ['zoom'],
                        280, 4,
                        200, 5,
                        180, 6,
                        150, 7,
                        100, 8,
                        0
                    ]
                ]
            ],
            layout: {
                'icon-image': [
                    'match',
                    ['get', 'vessel_type'],
                    80, 'ship-red', 81, 'ship-red', 82, 'ship-red', 83, 'ship-red', 84, 'ship-red', 85, 'ship-red', 86, 'ship-red', 87, 'ship-red', 88, 'ship-red', 89, 'ship-red',
                    70, 'ship-green', 71, 'ship-green', 72, 'ship-green', 73, 'ship-green', 74, 'ship-green', 75, 'ship-green', 76, 'ship-green', 77, 'ship-green', 78, 'ship-green', 79, 'ship-green',
                    'ship-purple'
                ],
                'icon-size': 0.6,
                'icon-rotate': ['get', 'heading'],
                'icon-rotation-alignment': 'map',
                'icon-allow-overlap': true,
                'icon-ignore-placement': true
            }
        });

        console.log("🗺️ [Shipping] Color-coded icons successfully rendered.");

    } catch (err) {
        console.error("❌ [Shipping] Error:", err);
    }

    // 4. Interaction: Hover events (replaces click)
    map.on('mouseenter', 'ships-layer', (e) => {
        map.getCanvas().style.cursor = 'pointer';
        const ship = e.features[0].properties;
        const coordinates = e.features[0].geometry.coordinates.slice();

        popup.setLngLat(coordinates)
            .setHTML(`
                <div style="font-family: sans-serif; font-size: 12px; color: #000; padding: 5px;">
                    <strong style="color: #007bff; font-size: 14px;">${ship.name}</strong><br>
                    <span style="color: #666;">Class:</span> ${ship.vessel_class}<br>
                    <span style="color: #666;">Dest:</span> ${ship.destination}<br>
                    <hr style="margin: 5px 0;">
                    <span style="color: #666;">MMSI:</span> ${ship.mmsi} | 
                    <span style="color: #666;">IMO:</span> ${ship.imo}<br>
                    <span style="color: #666;">Callsign:</span> ${ship.callsign}<br>
                    <span style="color: #666;">Draught:</span> ${ship.draught}m | 
                    <span style="color: #666;">Heading:</span> ${ship.heading}°
                </div>
            `)
            .addTo(map);
    });

    map.on('mouseleave', 'ships-layer', () => {
        map.getCanvas().style.cursor = '';
        popup.remove();
    });
}