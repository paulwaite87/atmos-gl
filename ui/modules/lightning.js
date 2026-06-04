// ui/modules/lightning.js

export function loadLayer(map, config) {
    const zoom = config.icon_zoom || 1.0;
    const baseSize = 30;
    const size = Math.floor(baseSize * zoom);

    // Fetch the data from the API endpoint
    fetch('http://localhost:9000/' + config.outfile + '?t=' + Date.now())
        .then(res => res.json())
        .then(strikes => { // Corrected parameter name from 'quakes' to 'strikes'
            console.log(`[Lightning] Rendering ${strikes.length} markers...`);

            strikes.forEach(d => {
                // Scaled Container
                const container = document.createElement('div');
                container.style.width = `${size}px`;
                container.style.height = `${size}px`;
                container.style.display = 'block';
                container.style.cursor = 'pointer';

                // Scaled Image logic
                const img = document.createElement('img');
                if (d.age_minutes <= config.strike_recent_minutes) {
                    img.src = window.location.origin + `/images/bolt_white.png`;
                } else if (d.age_minutes <= config.strike_keep_minutes) {
                    img.src = window.location.origin + `/images/bolt_yellow.png`;
                } else {
                    img.src = window.location.origin + `/images/bolt_red.png`;
                }
                img.style.width = `${size}px`;
                img.style.height = `${size}px`;
                img.style.display = 'block';
                img.style.objectFit = 'contain';

                container.appendChild(img);

                // Age Display
                const ageDisplay = d.age_minutes < 60
                    ? `${Math.floor(d.age_minutes)} mins ago`
                    : `${d.age_hours} ${d.age_hours === 1 ? 'hour' : 'hours'} ago`;

                // 4. Native Popup
                // FIX: Use d.label which already contains the formatted "Strike at HH:MM"
                const popup = new maplibregl.Popup({
                    offset: (size / 2),
                    closeButton: false,
                    className: 'lightning-popup'
                }).setHTML(`
                    <div style="font-family: sans-serif; font-size: 12px; color: #000; padding: 5px;">
                        <strong style="color: #ff4a4a; font-size: 14px;">${d.label}</strong> 
                        <hr style="border: 0; border-top: 1px solid #ccc; margin: 6px 0;">
                        <div><span style="color: #666; width: 65px; display: inline-block;">Age:</span> <strong style="color: ${d.is_recent ? '#28a745' : '#d9534f'};">${ageDisplay}</strong></div>
                    </div>
                `);

                // Marker
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
        .catch(err => console.error("[Lightning] Error:", err));
}