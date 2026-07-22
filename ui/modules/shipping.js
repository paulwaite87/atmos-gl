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
    const trackSourceId = 'ships-track-source';
    const trackLayerId  = 'ships-track-layer';
    let stopPopup = null;
    let currentCfg = config;
    let hoveredMmsi = null;

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

    // Hover-only track (shipping.view_tracks/track_limit/track_color): empty until a
    // ship is hovered, cleared again on mouseleave -- never a persistent overlay, so
    // there's exactly one track shown at a time (see mount()'s docstring context).
    const emptyTrack = () => ({ type: 'FeatureCollection', features: [] });

    const trackUrlFor = (mmsi, limit) =>
        `${window.WM_API}/ships/${mmsi}/track?limit=${limit}&t=${Date.now()}`;

    const showTrack = async (mmsi) => {
        hoveredMmsi = mmsi;
        let points;
        try {
            const resp = await fetchOrThrow(trackUrlFor(mmsi, currentCfg.track_limit || 50));
            points = resp.data || [];
        } catch (err) {
            console.warn(`[shipping] track fetch failed for ${mmsi}`, err);
            return;
        }
        // The hover may have moved to a different ship (or left entirely) while this
        // request was in flight -- a stale response must not overwrite what's shown.
        if (hoveredMmsi !== mmsi) return;
        if (!map.getSource(trackSourceId)) return;

        // newest-first from the API -- reverse so the line is drawn oldest -> newest.
        const coords = points.slice().reverse().map(p => [p.lon, p.lat]);
        map.getSource(trackSourceId).setData(coords.length >= 2
            ? { type: 'FeatureCollection', features: [
                { type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} },
            ] }
            : emptyTrack());
    };

    const clearTrack = () => {
        hoveredMmsi = null;
        map.getSource(trackSourceId)?.setData(emptyTrack());
    };

    // Bespoke mouseenter/mouseleave wiring rather than widening hoverPopup's contract
    // -- see docs/adr/0002-dont-extend-hoverpopup-for-markers.md; showing a track is a
    // different shape of "thing to do on hover" than hoverPopup's one job (an HTML
    // popup), so it stays alongside it as its own pair of handlers on the same layer.
    const onTrackEnter = (e) => {
        if (!currentCfg.view_tracks || !e.features.length) return;
        const mmsi = e.features[0].properties.mmsi;
        if (mmsi) showTrack(mmsi);
    };
    const onTrackLeave = () => clearTrack();

    const mount = async (cfg) => {
        currentCfg = cfg;
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

        // Added BEFORE layerId so the line renders underneath the ship icons.
        map.addSource(trackSourceId, { type: 'geojson', data: emptyTrack() });
        map.addLayer({
            id: trackLayerId, type: 'line', source: trackSourceId,
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': cfg.track_color || 'white', 'line-width': 2 },
        }, layerId);

        stopPopup = hoverPopup(map, layerId, { offset: 0, html: popupHtml });
        map.on('mouseenter', layerId, onTrackEnter);
        map.on('mouseleave', layerId, onTrackLeave);
    };

    const refresh = async (cfg) => {
        currentCfg = cfg;
        const data = await fetchData();
        map.getSource(sourceId)?.setData(data);
        if (map.getLayer(trackLayerId)) {
            map.setPaintProperty(trackLayerId, 'line-color', cfg.track_color || 'white');
        }
    };

    const unmount = () => {
        stopPopup?.();
        map.off('mouseenter', layerId, onTrackEnter);
        map.off('mouseleave', layerId, onTrackLeave);
        hoveredMmsi = null;
        if (map.getLayer(trackLayerId))   map.removeLayer(trackLayerId);
        if (map.getSource(trackSourceId)) map.removeSource(trackSourceId);
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, { sectionKey: 'shipping', initialConfig: config, mount, refresh, unmount, refreshMs: 60000 });
}