import { liveDataSync } from './_datasync.js';

export function loadLayer(map, config) {
    const sourceId = 'quakes-source';
    const layerId  = 'quakes-layer';
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 15 });
    const quakeIcons = [
        { id: 'quake-new', url: '/images/earthquake_new.png' },
        { id: 'quake-old', url: '/images/earthquake_old.png' },
    ];

    const urlFor = (cfg) => `${window.WM_API}/quakes/geojson`
        + `?min_mag=${cfg.min_mag ?? 3.5}`
        + `&expiry_hours=${cfg.expiry_hours ?? 12}`
        + `&recent_hours=${cfg.recent_activity_hours ?? 3}&t=${Date.now()}`;

    const fetchData = async (cfg) => {
        const r = await fetch(urlFor(cfg));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    };

    // named handlers so unmount can map.off() them
    const onEnter = (e) => {
        if (!e.features.length) return;
        map.getCanvas().style.cursor = 'pointer';
        const d = e.features[0].properties;
        const coords = e.features[0].geometry.coordinates.slice();
        const mins = Math.floor(d.age_minutes);
        const age = mins < 60 ? `${mins} mins ago` : `${Math.floor(mins/60)} hours ago`;
        popup.setLngLat(coords).setHTML(
            `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:5px;">
               <strong style="color:#ff4a4a;">M ${Number(d.mag).toFixed(1)}</strong> — ${d.place}
               <hr style="margin:6px 0;"><div>Depth: <strong>${d.depth} km</strong></div>
               <div>Age: <strong>${age}</strong></div></div>`).addTo(map);
    };
    const onLeave = () => { map.getCanvas().style.cursor = ''; popup.remove(); };

    const mount = async (cfg) => {
        await Promise.all(quakeIcons.map(async (ic) => {
            if (map.hasImage(ic.id)) return;
            const blob = await (await fetch(`${window.location.origin}${ic.url}`)).blob();
            map.addImage(ic.id, await createImageBitmap(blob));
        }));
        const data = await fetchData(cfg);
        if (map.getSource(sourceId)) return;          // guard against races
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId,
            layout: {
                'icon-image': ['case', ['get', 'is_recent'], 'quake-new', 'quake-old'],
                'icon-size': 0.8 * (cfg.icon_zoom ?? 1.0),
                'icon-allow-overlap': true, 'icon-ignore-placement': true,
            },
        });
        map.on('mouseenter', layerId, onEnter);
        map.on('mouseleave', layerId, onLeave);
    };

    const refresh = async (cfg) => {
        const data = await fetchData(cfg);
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        map.off('mouseenter', layerId, onEnter);
        map.off('mouseleave', layerId, onLeave);
        popup.remove();
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    liveDataSync(map, { sectionKey: 'quakes', initialConfig: config, mount, refresh, unmount, refreshMs: 60000 });
}