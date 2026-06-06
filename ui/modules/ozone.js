import { liveLayerSync } from './_refresh.js';

export function loadLayer(map, config) {
    const sourceId = 'ozone-source';
    const layerId  = 'ozone-layer';
    const slotId   = 'ozone-legend-slot';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];
    const urlFor = (cfg) => `${window.MAP_UI}/${cfg.outfile}`;
    const keyUrlFor = (cfg) => {
        const o = cfg.outfile, i = o.lastIndexOf('.');
        const base = i !== -1 ? o.slice(0, i) : o;
        const ext  = i !== -1 ? o.slice(i)    : '';
        return `${window.MAP_UI}/${base}_key${ext}`;
    };

    const addLegend = (cfg) => {
        const stack = document.getElementById('legend-stack');
        if (!stack) return;
        document.getElementById(slotId)?.remove();
        const slot = document.createElement('div');
        slot.id = slotId; slot.className = 'legend-slot';
        const img = document.createElement('img');
        img.src = `${keyUrlFor(cfg)}?t=${Date.now()}`;
        img.style.display = 'block'; img.style.width = '100%';
        slot.appendChild(img); stack.appendChild(slot);
    };

    const mount = (cfg) => {
        if (!map.getSource(sourceId)) {
            map.addSource(sourceId, { type: 'image', url: `${urlFor(cfg)}?t=${Date.now()}`, coordinates });
            map.addLayer({ id: layerId, type: 'raster', source: sourceId,
                           paint: { 'raster-opacity': 0.85, 'raster-fade-duration': 0 } });
        }
        addLegend(cfg);
    };

    const refresh = (cfg) => {
        const s = map.getSource(sourceId);
        if (s) s.updateImage({ url: `${urlFor(cfg)}?t=${Date.now()}` });
        addLegend(cfg);  // refresh the key image too (re-adds the slot with a new timestamp)
    };

    const unmount = () => {
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
        document.getElementById(slotId)?.remove();
    };

    liveLayerSync(map, {
        sectionKey: 'ozone', initialConfig: config,
        mount, refresh, unmount, imageUrl: urlFor,
    });
}