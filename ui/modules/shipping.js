// ui/modules/shipping.js

export function loadLayer(world, config) {
    // Cache buster included for immediate local updates!
    fetch('http://localhost:9000/' + config.outfile + '?t=' + Date.now())
        .then(res => res.json())
        .then(ships => {
            world
                .htmlElementsData(ships)
                .htmlLat('lat')
                .htmlLng('lng')
                .htmlElement(d => {
                    const container = document.createElement('div');
                    container.style.position = 'relative';
                    container.style.pointerEvents = 'auto'; // Break overlay lock

                    const img = document.createElement('img');

                    // Construct the image name based on your existing asset naming convention
                    // e.g., "red_ship_base.png"
                    img.src = `/images/${d.color_base}_ship_base.png`;

                    // Set a base size for the arrows
                    img.style.width = '14px';
                    img.style.height = '14px';
                    img.style.display = 'block';
                    img.style.cursor = 'pointer';

                    // THE MAGIC TRICK: Let CSS hardware rotate the image instantly!
                    img.style.transform = `rotate(${d.heading}deg)`;

                    // Hover transitions
                    img.style.transition = 'transform 0.15s ease, filter 0.15s ease';

                    // Build the detailed ship registry tooltip
                    const tooltip = document.createElement('div');
                    tooltip.innerHTML = `
                        <strong style="color: #64ffda; font-size: 14px; text-transform: uppercase;">${d.name}</strong> 
                        <span style="color: #888; font-size: 11px; margin-left: 6px;">MMSI: ${d.mmsi}</span>
                        <hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.1); margin: 6px 0;">
                        <div style="display: grid; grid-template-columns: 80px 1fr; gap: 4px; line-height: 1.4;">
                            <span style="color: #aaa;">Class:</span> <strong>${d.expanded_type || d.type}</strong>
                            <span style="color: #aaa;">Status:</span> <strong style="color: ${d.status === 'Underway' ? '#50fa7b' : '#ffb86c'};">${d.status}</strong>
                            <span style="color: #aaa;">Heading:</span> <strong>${d.heading}°</strong>
                            <span style="color: #aaa;">Dimensions:</span> <strong>${d.length}m x ${d.beam}m</strong>
                        </div>
                    `;

                    // Tooltip Styling
                    tooltip.style.position = 'absolute';
                    tooltip.style.bottom = '20px';
                    tooltip.style.left = '50%';
                    tooltip.style.transform = 'translateX(-50%)';
                    tooltip.style.background = 'rgba(10, 15, 25, 0.95)';
                    tooltip.style.color = '#ffffff';
                    tooltip.style.padding = '12px 16px';
                    tooltip.style.borderRadius = '6px';
                    tooltip.style.border = '1px solid rgba(100, 255, 218, 0.3)';
                    tooltip.style.fontFamily = 'system-ui, -apple-system, sans-serif';
                    tooltip.style.fontSize = '12px';
                    tooltip.style.whiteSpace = 'nowrap';
                    tooltip.style.boxShadow = '0 6px 16px rgba(0,0,0,0.8)';
                    tooltip.style.display = 'none';
                    tooltip.style.pointerEvents = 'none';
                    tooltip.style.zIndex = '99999';

                    // Hover Mechanics
                    container.addEventListener('mouseenter', () => {
                        tooltip.style.display = 'block';
                        // We must maintain the heading rotation while scaling up!
                        img.style.transform = `rotate(${d.heading}deg) scale(1.4)`;
                        img.style.filter = 'drop-shadow(0 0 4px rgba(255,255,255,0.5))';
                    });

                    container.addEventListener('mouseleave', () => {
                        tooltip.style.display = 'none';
                        img.style.transform = `rotate(${d.heading}deg) scale(1.0)`;
                        img.style.filter = 'none';
                    });

                    container.appendChild(img);
                    container.appendChild(tooltip);
                    return container;
                });

            console.log(`Shipping layer loaded: ${ships.length} vessels tracked.`);
        })
        .catch(err => console.log("Waiting for " + config.outfile + "...", err));
}