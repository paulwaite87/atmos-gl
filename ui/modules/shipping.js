// ui/modules/shipping.js

export function loadLayer(map, config) {
    // Default zoom to 1.0 if not provided
    const zoom = config.icon_zoom || 1.0;
    const baseSize = 14;
    const size = Math.floor(baseSize * zoom);

    fetch('http://localhost:9000/' + config.outfile + '?t=' + Date.now())
        .then(res => res.json())
        .then(ships => {
            ships.forEach(d => {
                // 1. Scaled Container
                const container = document.createElement('div');
                container.style.width = `${size}px`;
                container.style.height = `${size}px`;
                container.style.cursor = 'pointer';

                // 2. Scaled Image
                const img = document.createElement('img');
                img.src = `/images/${d.color_base}_ship_base.png`;
                img.style.width = `${size}px`;
                img.style.height = `${size}px`;
                img.style.display = 'block';
                img.style.transform = `rotate(${d.heading}deg)`;
                img.style.transition = 'transform 0.15s ease';

                container.appendChild(img);

                // 3. Popup (Remains standard size)
                const popup = new maplibregl.Popup({
                    offset: (size / 2), // Adjust offset so popup doesn't overlap the scaled icon
                    closeButton: false,
                    className: 'ship-popup'
                }).setHTML(`
                    <div style="font-family: sans-serif; font-size: 12px; color: #000; padding: 5px;">
                        <strong style="font-size: 14px; text-transform: uppercase; color: #000;">${d.name}</strong> 
                        <span style="color: #666; font-size: 11px; margin-left: 6px;">MMSI: ${d.mmsi}</span>
                        <hr style="border: 0; border-top: 1px solid #ccc; margin: 6px 0;">
                        <div style="display: grid; grid-template-columns: 80px 1fr; gap: 4px; line-height: 1.4;">
                            <span style="color: #666;">Class:</span> <strong>${d.expanded_type || d.type}</strong>
                            <span style="color: #666;">Status:</span> <strong style="color: ${d.status === 'Underway' ? '#28a745' : '#d9534f'};">${d.status}</strong>
                            <span style="color: #666;">Heading:</span> <strong>${d.heading}°</strong>
                            <span style="color: #666;">Dimensions:</span> <strong>${d.length}m x ${d.beam}m</strong>
                        </div>
                    </div>
                `);

                // 4. Marker with 'bottom' anchor
                const marker = new maplibregl.Marker({
                    element: container,
                    anchor: 'bottom',
                    opacityWhenCovered: 0
                })
                .setLngLat([d.lng, d.lat])
                .setPopup(popup)
                .addTo(map);

                // Interaction
                container.addEventListener('mouseenter', () => {
                    marker.togglePopup();
                    img.style.transform = `rotate(${d.heading}deg) scale(1.4)`;
                });

                container.addEventListener('mouseleave', () => {
                    marker.togglePopup();
                    img.style.transform = `rotate(${d.heading}deg) scale(1.0)`;
                });
            });
        })
        .catch(err => console.log("Waiting for " + config.outfile + "...", err));
}