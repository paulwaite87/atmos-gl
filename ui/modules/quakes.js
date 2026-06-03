// ui/modules/quakes.js

export function loadLayer(map, config) {
    // Default zoom to 1.0 if not provided
    const zoom = config.icon_zoom || 1.0;
    const baseSize = 30;
    const size = Math.floor(baseSize * zoom);

    fetch('http://localhost:9000/' + config.outfile + '?t=' + Date.now())
        .then(res => res.json())
        .then(quakes => {
            console.log(`[Quakes] Rendering ${quakes.length} markers...`);

            quakes.forEach(d => {
                // 1. Scaled Container
                const container = document.createElement('div');
                container.style.width = `${size}px`;
                container.style.height = `${size}px`;
                container.style.display = 'block';
                container.style.cursor = 'pointer';

                // 2. Scaled Image
                const img = document.createElement('img');
                img.src = window.location.origin + (d.is_recent ? '/images/earthquake_new.png' : '/images/earthquake_old.png');
                img.style.width = `${size}px`;
                img.style.height = `${size}px`;
                img.style.display = 'block';
                img.style.objectFit = 'contain';

                container.appendChild(img);

                // 3. Age Display
                const ageDisplay = d.age_minutes < 60
                    ? `${d.age_minutes} mins ago`
                    : `${d.age_hours} ${d.age_hours === 1 ? 'hour' : 'hours'} ago`;

                // 4. Native Popup
                const popup = new maplibregl.Popup({
                    offset: (size / 2), // Offset scales with icon size
                    closeButton: false,
                    className: 'quake-popup'
                }).setHTML(`
                    <div style="font-family: sans-serif; font-size: 12px; color: #000; padding: 5px;">
                        <strong style="color: #ff4a4a; font-size: 14px;">M ${d.mag.toFixed(1)}</strong> 
                        <span style="color: #666; margin-left: 4px;">— ${d.place}</span>
                        <hr style="border: 0; border-top: 1px solid #ccc; margin: 6px 0;">
                        <div><span style="color: #666; width: 65px; display: inline-block;">Depth:</span> <strong>${d.depth} km</strong></div>
                        <div><span style="color: #666; width: 65px; display: inline-block;">Age:</span> <strong style="color: ${d.is_recent ? '#28a745' : '#d9534f'};">${ageDisplay}</strong></div>
                    </div>
                `);

                // 5. Marker with 'bottom' anchor and 'ghosting' fix
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
                    img.style.transform = 'scale(1.2)';
                    img.style.transition = 'transform 0.2s';
                });

                container.addEventListener('mouseleave', () => {
                    marker.togglePopup();
                    img.style.transform = 'scale(1.0)';
                });
            });
        })
        .catch(err => console.error("[Quakes] Error:", err));
}