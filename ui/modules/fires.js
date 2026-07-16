import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { fetchOrThrow, popupCard } from './_feedhelpers.js';
import { createFillLayer } from './_webglfill.js';
import { buildThresholdLUT } from './_thresholdpalette.js';
import { keyFilename, showLegend, removeLegend } from './_legend.js';
import { opacityUniform } from './_opacity.js';

// Friendly names for the satellite codes FIRMS reports (VIIRS_NOAA20_NRT's "satellite"
// column -- see collectors/fires.py). Unrecognised codes fall through to the raw value.
const SATELLITE_NAMES = { N20: 'NOAA-20', N21: 'NOAA-21', N: 'Suomi NPP' };

// Fire Weather Index heatmap range/palette -- mirrors tasks/fire_weather.py's
// FIRE_WEATHER_SPEC exactly (0-100 FFWI scale, pale yellow at the risk threshold ->
// deep red at vmax) so the GPU layer matches the backend's static render.
const FWI_VMIN = 0.0;
const FWI_VMAX = 100.0;
const FWI_PALETTE = [[1.0, 0.95, 0.6], [1.0, 0.55, 0.1], [0.6, 0.0, 0.0]];
const FWI_FLAT = [0.0, 0.0, 0.0, 0.0];

export function loadLayer(map, config, fullConfig = {}) {
    // Renders as TWO GPU sub-layers under one "fires" config section/toggle -- the risk
    // heatmap (createFillLayer, below) and the hotspot dots (this circle layer) --
    // mirroring waves.js's combined heat-fill + particle-bars shape. The circle layer's
    // ids are prefixed "-points-" (not the bare "fires-source"/"fires-layer" the old
    // single-layer version used) because createFillLayer's own static-PNG fallback path
    // (used when WebGL fails or forecast_stepping is off) internally claims
    // "fires-source"/"fires-layer" for ITSELF when given sectionKey: 'fires' -- bare
    // reuse of those ids here would collide with it.
    const sourceId = 'fires-points-source';
    const layerId  = 'fires-points-layer';
    let stopPopup = null;

    const urlFor = (cfg) => `${window.WM_API}/fires/geojson`
        + `?min_confidence=${cfg.min_confidence ?? 'nominal'}`
        + `&expiry_hours=${cfg.expiry_hours ?? 24}`
        + `&max_frp=${cfg.max_frp ?? 5000}`
        + `&min_risk=${cfg.min_risk_filter ?? 0}&t=${Date.now()}`;

    const fetchData = (cfg) => fetchOrThrow(urlFor(cfg));

    const popupHtml = (f) => {
        const d = f.properties;
        const mins = Math.floor(d.age_minutes);
        const age = mins < 60 ? `${mins} mins ago` : `${Math.floor(mins / 60)} hours ago`;
        // width: 140 (default is 45, tuned for other layers' short labels like "VEI") --
        // "Fire Radiative Power" is the longest label here; every row shares one width
        // so the popup's label column stays aligned.
        return popupCard({
            title: 'Active Fire',
            titleColor: '#ff5a1f',
            rows: [
                { label: 'Confidence', value: d.confidence, width: 140 },
                { label: 'Fire Risk', value: d.fire_risk != null ? Number(d.fire_risk).toFixed(0) : 'N/A', width: 140 },
                { label: 'Fire Radiative Power', value: `${Number(d.frp).toFixed(1)} MW`, width: 140 },
                { label: 'Brightness', value: `${Number(d.brightness).toFixed(0)} K`, width: 140 },
                { label: 'Satellite', value: SATELLITE_NAMES[d.satellite] || d.satellite, width: 140 },
                { label: 'Day/Night', value: d.daynight === 'D' ? 'Day' : d.daynight === 'N' ? 'Night' : d.daynight, width: 140 },
                { label: 'Detected', value: age, width: 140 },
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

    const stopHotspots = liveDataSync(map, {
        sectionKey: 'fires', initialConfig: config, mount, refresh, unmount, refreshMs: 60000,
    });

    // Fire Weather Index heatmap -- same "fires" config section, so it mounts/refreshes
    // in lockstep with the hotspots above via its own independent liveLayerSync
    // subscription (createFillLayer's internal machinery). beforeId inserts it BENEATH
    // the hotspot circle layer, so dots always render on top of the risk shading.
    const legendSlotId = 'fires-legend-slot';
    const setLegend = (cfg) => {
        showLegend(legendSlotId, `${window.MAP_UI}/${keyFilename(cfg.outfile)}?t=${Date.now()}`);
    };

    const stopHeatmap = createFillLayer(map, {
        sectionKey: 'fires',
        initialConfig: config,
        initialAnimation: fullConfig.animation || {},
        initialCommon: fullConfig.common || {},
        vmin: FWI_VMIN,
        vspan: FWI_VMAX - FWI_VMIN,
        bicubic: true,
        beforeId: layerId,
        opacity: opacityUniform(config, 0.7),
        fragmentBody: `
            uniform float u_alpha;
            vec4 shade(float value, vec2 uv) {
                float t = clamp((value - ${FWI_VMIN.toFixed(1)}) / ${(FWI_VMAX - FWI_VMIN).toFixed(1)}, 0.0, 1.0);
                vec4 c = texture(u_cmap, vec2(t, 0.5));
                return vec4(c.rgb, c.a * u_alpha);
            }`,
        customUniforms: (cfg) => ({
            u_alpha: opacityUniform(cfg, 0.7),
        }),
        colormap: (cfg) => buildThresholdLUT({
            vmin: FWI_VMIN, vmax: FWI_VMAX,
            threshold: Number(cfg.min_risk_display) || 25.0,
            focus: 'above',
            paletteColors: FWI_PALETTE,
            flatColor: FWI_FLAT,
        }),
        onMount: setLegend,
        onRefresh: setLegend,
        onUnmount: () => removeLegend(legendSlotId),
    });

    return () => {
        try { stopHotspots && stopHotspots(); } catch (e) {}
        try { stopHeatmap && stopHeatmap(); } catch (e) {}
    };
}
