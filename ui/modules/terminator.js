import { liveDataSync } from './_datasync.js';

export function loadLayer(map, config) {
    const sourceId = 'terminator-source';
    const layerIds = ['terminator-night', 'terminator-edge'];

    const num = (v, d) => { const n = parseFloat(v); return Number.isFinite(n) ? n : d; };
    const paintFrom = (c) => {
        // alpha is on the standardised 0-100 UI scale; convert to 0-1.
        const opacity = Math.max(0, Math.min(100, num(c && c.alpha, 40))) / 100;
        // edge_softness feathers the day/night boundary. A blurred line ONLY softens if
        // it's wide enough that the blur forms a visible band over the fill's hard edge;
        // blurring a thin (6px) line just fades it to nothing and the crisp fill edge
        // shows through. So softness drives BOTH the band width and the blur radius.
        const soft = Math.max(0, num(c && c.edge_softness, 14));
        return {
            opacity,
            color: (c && c.shade_color) || '#070b18',
            lineWidth: Math.max(2, soft * 1.5),   // band wide enough to cover the seam
            lineBlur:  soft,                       // feather across that band
        };
    };

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
        // Wide, blurred line over the terminator: forms a soft glow band that feathers
        // the fill's otherwise-hard day/night seam. Width + blur both scale with softness.
        map.addLayer({
            id: 'terminator-edge', type: 'line', source: sourceId,
            filter: ['==', 'feature_type', 'TERMINATOR'],
            paint: {
                'line-color': p.color, 'line-width': p.lineWidth,
                'line-blur': p.lineBlur, 'line-opacity': p.opacity,
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
            map.setPaintProperty('terminator-edge', 'line-width', p.lineWidth);
            map.setPaintProperty('terminator-edge', 'line-blur', p.lineBlur);
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