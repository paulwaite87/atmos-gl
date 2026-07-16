import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, popupCard } from './_feedhelpers.js';

// Friendly names for the satellite codes FIRMS reports (VIIRS_NOAA20_NRT's "satellite"
// column -- see collectors/fires.py). Unrecognised codes fall through to the raw value.
const SATELLITE_NAMES = { N20: 'NOAA-20', N21: 'NOAA-21', N: 'Suomi NPP' };

export function loadLayer(map, config) {
    const sourceId = 'fires-source';
    const layerId  = 'fires-layer';
    let stopPopup = null;

    const urlFor = (cfg) => `${window.WM_API}/fires/geojson`
        + `?min_confidence=${cfg.min_confidence ?? 'nominal'}`
        + `&expiry_hours=${cfg.expiry_hours ?? 24}`
        + `&max_frp=${cfg.max_frp ?? 5000}&t=${Date.now()}`;

    const fetchData = (cfg) => fetchOrThrow(urlFor(cfg));

    const popupHtml = (f) => {
        const d = f.properties;
        const mins = Math.floor(d.age_minutes);
        const age = mins < 60 ? `${mins} mins ago` : `${Math.floor(mins / 60)} hours ago`;
        // width: 75 (default is 45, tuned for other layers' short labels like "VEI") --
        // "Confidence"/"Brightness" clip and run into the value at the default width.
        return popupCard({
            title: 'Active Fire',
            titleColor: '#ff5a1f',
            rows: [
                { label: 'Confidence', value: d.confidence, width: 75 },
                { label: 'FRP', value: `${Number(d.frp).toFixed(1)} MW`, width: 75 },
                { label: 'Brightness', value: `${Number(d.brightness).toFixed(0)} K`, width: 75 },
                { label: 'Satellite', value: SATELLITE_NAMES[d.satellite] || d.satellite, width: 75 },
                { label: 'Day/Night', value: d.daynight === 'D' ? 'Day' : d.daynight === 'N' ? 'Night' : d.daynight, width: 75 },
                { label: 'Detected', value: age, width: 75 },
            ],
        });
    };

    // No image assets -- fires render as a native circle layer (radius by FRP, color by
    // confidence tier) rather than an icon symbol layer like quakes/volcanoes.
    const mount = async (cfg) => {
        const data = await fetchData(cfg);
        if (map.getSource(sourceId)) return;          // guard against races
        map.addSource(sourceId, { type: 'geojson', data });
        map.addLayer({
            id: layerId, type: 'circle', source: sourceId,
            paint: {
                'circle-radius': ['interpolate', ['linear'], ['get', 'frp'], 0, 4, 50, 8, 500, 16],
                'circle-color': [
                    'match', ['get', 'confidence'],
                    'high', '#ff2b00',
                    'nominal', '#ff8c00',
                    'low', '#ffd166',
                    '#ff8c00',
                ],
                'circle-opacity': 0.75,
                'circle-stroke-width': 0.5,
                'circle-stroke-color': '#7a1a00',
            },
        });
        stopPopup = hoverPopup(map, layerId, { html: popupHtml });
    };

    const refresh = async (cfg) => {
        const data = await fetchData(cfg);
        map.getSource(sourceId)?.setData(data);
    };

    const unmount = () => {
        stopPopup?.();
        if (map.getLayer(layerId))   map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, { sectionKey: 'fires', initialConfig: config, mount, refresh, unmount, refreshMs: 60000 });
}
