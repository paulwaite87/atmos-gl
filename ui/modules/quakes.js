// ui/modules/quakes.js

export async function loadLayer(map, config) {
    const sourceId = 'quakes-source';
    const layerId = 'quakes-layer';

    // Pull configurations with fallbacks
    const minMag = config.min_mag || 3.5;
    const expiryHours = config.expiry_hours || 12;
    const recentHours = config.recent_activity_hours || 3;
    const zoomSize = config.icon_zoom || 1.0;

    const baseUrl = `${window.WM_API}/quakes/geojson`;
    const geojsonUrl = `${baseUrl}?min_mag=${minMag}&expiry_hours=${expiryHours}&recent_hours=${recentHours}`;

    if (map.getSource(sourceId)) return;

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 15 });

    const quakeIcons = [
        { id: 'quake-new', url: '/images/earthquake_new.png' },
        { id: 'quake-old', url: '/images/earthquake_old.png' }
    ];

    try {
        // 1. Load Images
        await Promise.all(quakeIcons.map(async (icon) => {
            const response = await fetch(`${window.location.origin}${icon.url}`);
            if (!response.ok) throw new Error(`Could not load ${icon.id}`);
            const blob = await response.blob();
            const bitmap = await createImageBitmap(blob);
            if (!map.hasImage(icon.id)) map.addImage(icon.id, bitmap);
        }));

        // 2. Fetch GeoJSON Data
        const geoResponse = await fetch(`${geojsonUrl}&t=${Date.now()}`);
        const geojsonData = await geoResponse.json();

        map.addSource(sourceId, { type: 'geojson', data: geojsonData });

        // 3. Render WebGL Layer
        map.addLayer({
            id: layerId,
            type: 'symbol',
            source: sourceId,
            layout: {
                // DATA-DRIVEN STYLING: Pick icon based on the boolean 'is_recent' field
                'icon-image': [
                    'case',
                    ['get', 'is_recent'], 'quake-new',
                    'quake-old' // default/fallback
                ],
                'icon-size': 0.8 * zoomSize,
                'icon-allow-overlap': true,
                'icon-ignore-placement': true
            }
        });

        console.log(`🌋 [Quakes] WebGL layer rendered with ${geojsonData.features.length} events.`);

    } catch (err) {
        console.error("❌ [Quakes] Error:", err);
    }

    // 4. Hover Interactions
    map.on('mouseenter', layerId, (e) => {
        map.getCanvas().style.cursor = 'pointer';
        const d = e.features[0].properties;
        const coordinates = e.features[0].geometry.coordinates.slice();

        const ageMins = Math.floor(d.age_minutes);
        const ageHours = Math.floor(ageMins / 60);
        const ageDisplay = ageMins < 60 ? `${ageMins} mins ago` : `${ageHours} ${ageHours === 1 ? 'hour' : 'hours'} ago`;

        popup.setLngLat(coordinates)
            .setHTML(`
                <div style="font-family: sans-serif; font-size: 12px; color: #000; padding: 5px;">
                    <strong style="color: #ff4a4a; font-size: 14px;">M ${d.mag.toFixed(1)}</strong> 
                    <span style="color: #666; margin-left: 4px;">— ${d.place}</span>
                    <hr style="border: 0; border-top: 1px solid #ccc; margin: 6px 0;">
                    <div><span style="color: #666; width: 65px; display: inline-block;">Depth:</span> <strong>${d.depth} km</strong></div>
                    <div><span style="color: #666; width: 65px; display: inline-block;">Age:</span> <strong style="color: ${d.is_recent ? '#28a745' : '#d9534f'};">${ageDisplay}</strong></div>
                </div>
            `)
            .addTo(map);
    });

    map.on('mouseleave', layerId, () => {
        map.getCanvas().style.cursor = '';
        popup.remove();
    });
}