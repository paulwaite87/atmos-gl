// ui/modules/volcanoes.js

export async function loadLayer(map, config) {
    const sourceId = 'volcanoes-source';
    const layerId = 'volcanoes-layer';

    if (map.getSource(sourceId)) return;

    // 1. Build URL
    const baseUrl = `${window.WM_API}/volcanoes/geojson`;
    const url = `${baseUrl}?vei_min=${config.vei_min}&significant=${config.significant}&codes=${config.erupt_date_codes.join(',')}`;

    // Create a single persistent popup instance shared across mouse events
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 15 });

    try {
        console.log(`[Volcanoes] Fetching dataset from: ${url}`);
        const data = await fetch(url).then(r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
        });

        console.log(`[Volcanoes] Features returned from API: ${data.features?.length || 0}`);

        const bindToMap = async () => {
            try {
                if (map.getSource(sourceId)) return;

                // Load and register icon texture
                const img = await fetch('/images/volcano_symbol.png').then(r => r.blob()).then(createImageBitmap);
                if (!map.hasImage('volcano-icon')) {
                    map.addImage('volcano-icon', img);
                }

                // Inject Source and Layer
                map.addSource(sourceId, { type: 'geojson', data });
                map.addLayer({
                    id: layerId,
                    type: 'symbol',
                    source: sourceId,
                    layout: {
                        'icon-image': 'volcano-icon',
                        'icon-size': 0.6,
                        'icon-allow-overlap': true,
                        'icon-ignore-placement': true
                    }
                });

                console.log(`[Volcanoes] Layer successfully mounted to map canvas.`);

                // 2. Hover Interactivity: Display on mouse entry
                map.on('mouseenter', layerId, (e) => {
                    if (!e.features.length) return;

                    map.getCanvas().style.cursor = 'pointer';

                    const feature = e.features[0];
                    const props = feature.properties;
                    const coordinates = feature.geometry.coordinates.slice();

                    // Populate and render the popup node over the volcano coordinates
                    popup.setLngLat(coordinates)
                        .setHTML(`
                            <div style="font-family: sans-serif; font-size: 12px; color: #000; padding: 3px;">
                                <strong style="font-size: 13px; color: #333;">${props.name || 'Unknown Volcano'}</strong>
                                <hr style="border: 0; border-top: 1px solid #ccc; margin: 4px 0;">
                                <div><span style="color: #666; width: 45px; display: inline-block;">VEI:</span> <strong>${props.vei}</strong></div>
                                <div><span style="color: #666; width: 45px; display: inline-block;">Code:</span> <strong>${props.code || 'N/A'}</strong></div>
                            </div>
                        `)
                        .addTo(map);
                });

                // 3. Hover Interactivity: Erase popup on mouse leave
                map.on('mouseleave', layerId, () => {
                    map.getCanvas().style.cursor = '';
                    popup.remove();
                });

            } catch (styleErr) {
                console.error("❌ [Volcanoes] Error inserting layer frames:", styleErr);
            }
        };

        if (map.loaded()) {
            await bindToMap();
        } else {
            map.once('load', bindToMap);
        }

    } catch (err) {
        console.error("❌ [Volcanoes] Core network fetch failure:", err);
    }
}