import { liveDataSync } from './_datasync.js';

export function loadLayer(map, config) {
    const sourceId = 'ships-source';
    const layerId  = 'ships-layer';
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
    const shipIcons = [
        { id: 'ship-red',    url: '/images/red_ship_base.png' },
        { id: 'ship-green',  url: '/images/green_ship_base.png' },
        { id: 'ship-purple', url: '/images/purple_ship_base.png' },
    ];

    const urlFor = () => `${window.WM_API}/ships/geojson?t=${Date.now()}`;

    const fetchData = async () => {
        const r = await fetch(urlFor());
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    };

    const onEnter = (e) => {
        if (!e.features.length) return;
        map.getCanvas().style.cursor = 'pointer';
        const s = e.features[0].properties;
        const coords = e.features[0].geometry.coordinates.slice();
        popup.setLngLat(coords).setHTML(
            `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:5px;">
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
                <span style="color:#666;">Speed:</span> ${s.speed}knots
            </div>`).addTo(map);
    };
    const onLeave = () => { map.getCanvas().style.cursor = ''; popup.remove(); };

    const mount = async () => {
        await Promise.all(shipIcons.map(async (ic) => {
            if (map.hasImage(ic.id)) return;
            const res = await fetch(`${window.location.origin}${ic.url}`);
            if (!res.ok) throw new Error(`Could not load ${ic.id}`);
            map.addImage(ic.id, await createImageBitmap(await res.blob()));
        }));
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
                'icon-size': 0.6,
                'icon-rotate': ['get', 'heading'],
                'icon-rotation-alignment': 'map',
                'icon-allow-overlap': true, 'icon-ignore-placement': true,
            },
        });
        map.on('mouseenter', layerId, onEnter);
        map.on('mouseleave', layerId, onLeave);
    };

    const refresh = async () => {
        const data = await fetchData();
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        map.off('mouseenter', layerId, onEnter);
        map.off('mouseleave', layerId, onLeave);
        popup.remove();
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    // Ships move — keep this fairly brisk.
    return liveDataSync(map, { sectionKey: 'shipping', initialConfig: config, mount, refresh, unmount, refreshMs: 60000 });
}