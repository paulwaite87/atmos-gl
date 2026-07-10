import { liveLayerSync } from './_refresh.js';
import { keyFilename, replaceSlot, removeLegend } from './_legend.js';

const MODES = ['absolute', 'anomaly'];

// Insert "_<mode>" before the extension: "data/sst.png" -> "data/sst_anomaly.png".
// The backend always keeps BOTH modes' renders fresh on disk (SstCollector fetches
// both netCDFs unconditionally; SSTUpdater renders both every cycle -- see
// tasks/sst.py), so this is a pure filename swap, no backend round-trip needed.
function modeFilename(outfile, mode) {
    const i = outfile.lastIndexOf('.');
    const base = i !== -1 ? outfile.slice(0, i) : outfile;
    const ext  = i !== -1 ? outfile.slice(i)    : '';
    return `${base}_${mode}${ext}`;
}

export function loadLayer(map, config) {
    const sourceId = 'sst-source';
    const layerId  = 'sst-layer';
    const slotId   = 'sst-legend-slot';
    const coordinates = [
        [-180, 85.051129], [180, 85.051129],
        [180, -85.051129], [-180, -85.051129],
    ];

    // Client-side selected mode, independent of the server config's `mode` setting --
    // clicking Absolute/Anomaly below swaps which pre-rendered image is shown
    // instantly. Defaults to whatever the config currently says, then stays wherever
    // the user last clicked (a config edit to `mode` elsewhere doesn't override it).
    let selectedMode = MODES.includes(config?.mode) ? config.mode : 'absolute';

    const urlFor = (cfg) => `${window.MAP_UI}/${modeFilename(cfg.outfile, selectedMode)}`;

    const apply = (cfg) => {
        const s = map.getSource(sourceId);
        if (s) s.updateImage({ url: `${urlFor(cfg)}?t=${Date.now()}` });
        addLegend(cfg);
    };

    const addLegend = (cfg) => {
        replaceSlot(slotId, (slot) => {
            const toggle = document.createElement('div');
            toggle.className = 'sst-mode-toggle';
            for (const mode of MODES) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.textContent = mode === 'absolute' ? 'Absolute' : 'Anomaly';
                btn.className = 'sst-mode-btn' + (mode === selectedMode ? ' active' : '');
                btn.onclick = () => {
                    if (selectedMode === mode) return;
                    selectedMode = mode;
                    apply(cfg);
                };
                toggle.appendChild(btn);
            }
            slot.appendChild(toggle);

            const img = document.createElement('img');
            img.src = `${window.MAP_UI}/${keyFilename(modeFilename(cfg.outfile, selectedMode))}?t=${Date.now()}`;
            img.style.display = 'block'; img.style.width = '100%';
            slot.appendChild(img);
        });
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
        apply(cfg);
    };

    const unmount = () => {
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
        removeLegend(slotId);
    };

    return liveLayerSync(map, { sectionKey: 'sst', initialConfig: config, mount, refresh, unmount, imageUrl: urlFor });
}
