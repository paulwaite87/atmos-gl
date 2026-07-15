import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, preloadIcons } from './_feedhelpers.js';

// Helper function: Parses time difference into '3d 13h 20 mins ago' formats
function formatLastUpdate(lastUpdateStr) {
    if (!lastUpdateStr) return 'Unknown';

    const lastUpdate = new Date(lastUpdateStr);
    if (isNaN(lastUpdate.getTime())) return 'Unknown';

    const now = new Date();
    const diffMs = Math.max(0, now - lastUpdate);
    const totalMins = Math.floor(diffMs / (1000 * 60));

    if (totalMins === 0) return 'Just now';

    const days = Math.floor(totalMins / (60 * 24));
    const hours = Math.floor((totalMins % (60 * 24)) / 60);
    const mins = totalMins % 60;

    const parts = [];

    if (days > 0) parts.push(`${days}d`);
    if (hours > 0) parts.push(`${hours}h`);
    if (mins > 0) parts.push(`${mins} mins`);

    return `${parts.join(' ')} ago`;
}

export function loadLayer(map, config) {
    const sourceId = 'ships-source';
    const layerId  = 'ships-layer';
    let stopPopup = null;

    // Set X days here, or pass it in via the config object
    const maxAgeDays = config.max_age_days || 7;

    const shipIcons = [
        { id: 'ship-red',    url: '/images/red_ship_base.png' },
        { id: 'ship-green',  url: '/images/green_ship_base.png' },
        { id: 'ship-purple', url: '/images/purple_ship_base.png' },
    ];

    const urlFor = () => `${window.WM_API}/ships/geojson?t=${Date.now()}`;

    const fetchData = async () => {
        const geojson = await fetchOrThrow(urlFor());

        // Calculate the absolute cutoff time in milliseconds
        const cutoffTimeMs = Date.now() - (maxAgeDays * 24 * 60 * 60 * 1000);

        // Filter the GeoJSON features based on the cutoff time
        geojson.features = geojson.features.filter(feature => {
            const updateStr = feature.properties.last_position_update;
            if (!updateStr) return false; // Exclude if there's no timestamp

            const updateTimeMs = new Date(updateStr).getTime();
            if (isNaN(updateTimeMs)) return false; // Exclude if date is invalid

            return updateTimeMs >= cutoffTimeMs;
        });

        return geojson;
    };

    const popupHtml = (f) => {
        const s = f.properties;
        const lastSeenText = formatLastUpdate(s.last_position_update);
        return `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:5px;">
                <strong style="color:#007bff;font-size:14px;">${s.name}</strong><br>
                <span style="color:#666;">Class:</span> ${s.vessel_class}<br>
                <span style="color:#666;">Dest:</span> ${s.destination}<br>
                <hr style="margin:5px 0;">
                <span style="color:#666;">MMSI:</span> ${s.mmsi} |
                <span style="color:#666;">IMO:</span> ${s.imo}<br>
                <span style="color:#666;">Callsign:</span> ${s.callsign}<br>
                <span style="color:#666;">Draught:</span> ${s.draught}m |
                <span style="color:#666;">Heading:</span> ${s.heading}°<br>
                <span style="color:#666;">Length:</span> ${s.length}m |
                <span style="color:#666;">Beam:</span> ${s.beam}m<br>
                <span style="color:#666;">Speed:</span> ${s.speed}knots<br>
                <span style="color:#666;">Last seen:</span> ${lastSeenText}
            </div>`;
    };

    const mount = async (cfg) => {
        await preloadIcons(map, shipIcons);

        const data = await fetchData();
        if (map.getSource(sourceId)) return;

        map.addSource(sourceId, { type: 'geojson', data, tolerance: 0.5 });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId, minzoom: 3,
            filter: ['all', ['>=', ['get', 'length'],
                ['step', ['zoom'], 280, 4, 200, 5, 180, 6, 150, 7, 100, 8, 0]]],
            layout: {
                'icon-image': ['match', ['get', 'vessel_type'],
                    80,'ship-red',81,'ship-red',82,'ship-red',83,'ship-red',84,'ship-red',85,'ship-red',86,'ship-red',87,'ship-red',88,'ship-red',89,'ship-red',
                    70,'ship-green',71,'ship-green',72,'ship-green',73,'ship-green',74,'ship-green',75,'ship-green',76,'ship-green',77,'ship-green',78,'ship-green',79,'ship-green',
                    'ship-purple'],
                'icon-size': 0.6 * (cfg.icon_zoom ?? 1.0),
                'icon-rotate': ['get', 'heading'],
                'icon-rotation-alignment': 'map',
                'icon-allow-overlap': true, 'icon-ignore-placement': true,
            },
        });

        stopPopup = hoverPopup(map, layerId, { offset: 0, html: popupHtml });
    };

    const refresh = async () => {
        const data = await fetchData();
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        stopPopup?.();
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, { sectionKey: 'shipping', initialConfig: config, mount, refresh, unmount, refreshMs: 60000 });
}