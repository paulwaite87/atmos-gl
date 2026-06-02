// ui/modules/quakes.js

export function loadLayer(world, config) {
    fetch('http://localhost:9000/' + config.outfile + '?t=' + Date.now())
        .then(res => res.json())
        .then(quakes => {
            console.log("=== RAW QUAKES DATA FETCHED ===", quakes);
            world
                .htmlElementsData(quakes)
                .htmlLat('lat')
                .htmlLng('lng')
                .htmlElement(d => {
                    // 1. Create the container that holds both the image and its tooltip
                    const container = document.createElement('div');
                    container.style.position = 'relative';

                    // CRITICAL FIX: Explicitly break out of Globe.gl's transparent overlay lock
                    // so this specific marker element can intercept mouse entry/exit events.
                    container.style.pointerEvents = 'auto';

                    // 2. Create the marker image
                    const img = document.createElement('img');
                    if (d.is_recent) {
                        img.src = '/images/earthquake_new.png';
                    } else {
                        img.src = '/images/earthquake_old.png';
                    }

                    const size = Math.max(14, d.mag * 5);
                    img.style.width = `${size}px`;
                    img.style.height = `${size}px`;
                    img.style.display = 'block';
                    img.style.cursor = 'pointer';
                    img.style.transition = 'transform 0.1s ease';

                    // 3. Create the styled tooltip box (hidden by default)
                    const tooltip = document.createElement('div');

                    let ageDisplay = d.age_minutes < 60
                        ? `${d.age_minutes} mins ago`
                        : `${d.age_hours} ${d.age_hours === 1 ? 'hour' : 'hours'} ago`;

                    tooltip.innerHTML = `
                        <strong style="color: #ff4a4a; font-size: 14px;">M ${d.mag.toFixed(1)}</strong> 
                        <span style="color: #888; margin-left: 4px;">— ${d.place}</span>
                        <hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.1); margin: 6px 0;">
                        <div><span style="color: #aaa; width: 65px; display: inline-block;">Depth:</span> <strong>${d.depth} km</strong></div>
                        <div><span style="color: #aaa; width: 65px; display: inline-block;">Age:</span> <strong style="color: ${d.is_recent ? '#50fa7b' : '#ffb86c'};">${ageDisplay}</strong></div>
                    `;

                    tooltip.style.position = 'absolute';
                    tooltip.style.bottom = `${size + 6}px`; // Float just above the icon
                    tooltip.style.left = '50%';
                    tooltip.style.transform = 'translateX(-50%)';
                    tooltip.style.background = 'rgba(4, 4, 14, 0.95)';
                    tooltip.style.color = '#ffffff';
                    tooltip.style.padding = '10px 14px';
                    tooltip.style.borderRadius = '6px';
                    tooltip.style.border = '1px solid rgba(255, 255, 255, 0.25)';
                    tooltip.style.fontFamily = 'system-ui, -apple-system, sans-serif';
                    tooltip.style.fontSize = '13px';
                    tooltip.style.whiteSpace = 'nowrap';
                    tooltip.style.boxShadow = '0 4px 12px rgba(0,0,0,0.6)';
                    tooltip.style.display = 'none'; // Hidden initially
                    tooltip.style.pointerEvents = 'none'; // Ensure tooltip layout itself won't intercept mouse focus
                    tooltip.style.zIndex = '99999';

                    // 4. Attach standard DOM hover listeners directly to the container
                    container.addEventListener('mouseenter', () => {
                        tooltip.style.display = 'block';
                        img.style.transform = 'scale(1.25)';
                    });

                    container.addEventListener('mouseleave', () => {
                        tooltip.style.display = 'none';
                        img.style.transform = 'scale(1.0)';
                    });

                    // Assemble the elements
                    container.appendChild(img);
                    container.appendChild(tooltip);

                    return container;
                });

            console.log("Earthquake layer loaded using native interactive DOM elements.");
        })
        .catch(err => console.log("Waiting for " + config.outfile + "...", err));
}