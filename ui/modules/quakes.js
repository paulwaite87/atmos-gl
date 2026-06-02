// static/quakes.js

// Standardized export name passing both the Globe instance and the specific module config
export function loadLayer(world, config) {
    fetch('http://localhost:9000/data/quakes.json')
        .then(res => res.json())
        .then(quakes => {
            world
                .labelsData(quakes)
                .labelLat('lat')
                .labelLng('lng')
                .labelText('label')
                .labelSize(d => Math.max(1, d.mag / 2))
                .labelDotRadius(d => d.mag / 3)
                // Use the color defined in worldmap.json!
                .labelColor(() => config.marker_color || 'rgba(255, 20, 147, 0.8)')
                .labelResolution(2)
                .labelLabel(d => `<strong>${d.label}</strong><br>Magnitude: ${d.mag}`);

            console.log("Earthquake layer loaded!");
        })
        .catch(err => console.log("Waiting for quakes.json to be generated...", err));
}