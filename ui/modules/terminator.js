import { liveDataSync } from './_datasync.js';

export function loadLayer(map, config) {
    const sourceId = 'terminator-source';
    const layerIds = ['terminator-night', 'terminator-edge'];

    const num = (v, d) => { const n = parseFloat(v); return Number.isFinite(n) ? n : d; };
    const paintFrom = (c) => ({
        opacity:  Math.max(0, Math.min(1, num(c && c.shade_opacity, 0.4))),
        color:    (c && c.shade_color) || '#070b18',
        softness: Math.max(0, num(c && c.edge_softness, 14)),
    });

    const fetchData = async () => {
        const r = await fetch(`${window.WM_API}/terminator/geojson?t=${Date.now()}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    };

    const mount = async (section) => {
        const data = await fetchData();
        if (map.getSource(sourceId)) return;
        const p = paintFrom(section);
        map.addSource(sourceId, { type: 'geojson', data });

        // Solid night side — hard edge exactly at the terminator.
        map.addLayer({
            id: 'terminator-night', type: 'fill', source: sourceId,
            filter: ['==', 'feature_type', 'NIGHT'],
            paint: { 'fill-color': p.color, 'fill-opacity': p.opacity },
        });
        // Blurred line on the terminator softens that edge into a fade.
        map.addLayer({
            id: 'terminator-edge', type: 'line', source: sourceId,
            filter: ['==', 'feature_type', 'TERMINATOR'],
            paint: {
                'line-color': p.color, 'line-width': 6,
                'line-blur': p.softness, 'line-opacity': p.opacity,
            },
        });
    };

    const refresh = async (section) => {
        const data = await fetchData();
        map.getSource(sourceId)?.setData(data);
        const p = paintFrom(section);                  // live tuning of all three knobs
        if (map.getLayer('terminator-night')) {
            map.setPaintProperty('terminator-night', 'fill-opacity', p.opacity);
            map.setPaintProperty('terminator-night', 'fill-color', p.color);
        }
        if (map.getLayer('terminator-edge')) {
            map.setPaintProperty('terminator-edge', 'line-opacity', p.opacity);
            map.setPaintProperty('terminator-edge', 'line-color', p.color);
            map.setPaintProperty('terminator-edge', 'line-blur', p.softness);
        }
    };

    const unmount = () => {
        for (const id of layerIds) if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, {
        sectionKey: 'terminator',
        initialConfig: config,
        mount, refresh, unmount,
        refreshMs: 30000,        // terminator moves ~0.25°/min; 30s reposition is seamless
    });
}