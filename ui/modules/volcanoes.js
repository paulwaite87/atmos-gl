// ui/modules/volcanoes.js

export async function loadLayer(map, config) {
    const sourceId = 'volcanoes-source';
    const layerId = 'volcanoes-layer';

    // API endpoint with parameters
    const baseUrl = `${window.WM_API}/volcanoes/geojson`;
    const url = `${baseUrl}?vei_min=${config.vei_min}&significant=${config.significant_only}&codes=${config.erupt_date_codes.join(',')}`;

    if (map.getSource(sourceId)) return;

    // Load icon
    const img = await fetch('/images/volcano.png').then(r => r.blob()).then(createImageBitmap);
    map.addImage('volcano-icon', img);

    const data = await fetch(url).then(r => r.json());

    map.addSource(sourceId, { type: 'geojson', data });

    map.addLayer({
        id: layerId,
        type: 'symbol',
        source: sourceId,
        layout: {
            'icon-image': 'volcano-icon',
            'icon-size': 0.6,
            'icon-allow-overlap': true
        }
    });

    // Simple popup
    map.on('click', layerId, (e) => {
        new maplibregl.Popup()
            .setLngLat(e.features[0].geometry.coordinates)
            .setHTML(`<strong>${e.features[0].properties.name}</strong><br>VEI: ${e.features[0].properties.vei}`)
            .addTo(map);
    });
}