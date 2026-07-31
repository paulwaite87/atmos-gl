import { liveDataSync } from './_datasync.js';
import { opacityUniform } from './_opacity.js';

// Coastline/lake-shore outlines, drawn from MapTiler's own OpenMapTiles vector tiles
// (independent of basemap.js's chosen style -- even the raster-only "satellite" style
// has no vector layers of its own to reuse) rather than a bundled static GeoJSON: a
// single-resolution file detailed enough to look sharp zoomed in (Natural Earth 10m)
// is tens of MB, and anything coarser looks blocky up close. Vector tiles give the
// zoom-appropriate resolution for free via MapLibre's own tile pyramid. The 'water'
// source-layer is a polygon layer (oceans/lakes/rivers) -- MapLibre happily strokes
// just a polygon's boundary when a 'line'-type layer references it, which is exactly
// the coastline.
export function loadLayer(map, config, fullConfig = {}) {
    const SRC = 'landmass-source';
    const HALO_LYR = 'landmass-halo';
    const LINE_LYR = 'landmass-line';

    const num = (v, d) => { const n = parseFloat(v); return Number.isFinite(n) ? n : d; };

    // Colour is a fixed pair (line + halo), not sampled from whatever's underneath --
    // reading back the WebGL framebuffer under a moving line every frame would be
    // expensive and fragile against the custom-WebGL raster layers sharing the canvas
    // (temperature.js etc.). A halo -- a slightly wider stroke in a contrasting colour
    // drawn beneath the main one -- stays legible over any background instead, the
    // same technique markers.js already uses for its label halos.
    const paintFrom = (cfg) => {
        const width = Math.max(0.2, num(cfg.linewidth, 1));
        return {
            color: cfg.color || 'White',
            haloColor: cfg.halo_color || 'Black',
            width,
            haloWidth: width + 1.5,
            opacity: opacityUniform(cfg, 0.9),
        };
    };

    // Keep landmass outlines above every data raster fill regardless of mount/refresh
    // order -- createFillLayer's addBelow (with no beforeId, which every fill layer
    // module uses today) always adds a new fill at the absolute top, so a layer
    // toggled on after landmass would otherwise bury it. Same problem markers.js
    // solves for its own dots/labels (see that module's ensureOnTop), but the two
    // modules want opposite outcomes at the very top of the stack, so this one is
    // satisfied by EITHER itself or markers being topmost (markers only accepts
    // itself). That asymmetry stops the two independent 'styledata' listeners
    // fighting forever: the only state that still makes landmass act is "some raster
    // fill is on top", and reclaiming the top either satisfies landmass outright or
    // hands markers' own listener a reason to reclaim it next -- which then satisfies
    // landmass's check on the following pass.
    const ensureOnTop = () => {
        const layers = map.getStyle()?.layers;
        if (!layers || !layers.length) return;
        const topId = layers[layers.length - 1].id;
        if (topId === LINE_LYR || topId === 'markers-labels') return;
        if (map.getLayer(HALO_LYR)) map.moveLayer(HALO_LYR);
        if (map.getLayer(LINE_LYR)) map.moveLayer(LINE_LYR);
    };

    const mount = async (cfg) => {
        if (map.getSource(SRC)) return;
        const key = fullConfig.common?.api_key || '';
        map.addSource(SRC, {
            type: 'vector',
            url: `https://api.maptiler.com/tiles/v3/tiles.json?key=${key}`,
        });
        const p = paintFrom(cfg);
        map.addLayer({
            id: HALO_LYR, type: 'line', source: SRC, 'source-layer': 'water',
            layout: { 'line-join': 'round', 'line-cap': 'round' },
            paint: {
                'line-color': p.haloColor, 'line-width': p.haloWidth, 'line-opacity': p.opacity,
            },
        });
        map.addLayer({
            id: LINE_LYR, type: 'line', source: SRC, 'source-layer': 'water',
            layout: { 'line-join': 'round', 'line-cap': 'round' },
            paint: {
                'line-color': p.color, 'line-width': p.width, 'line-opacity': p.opacity,
            },
        });
        ensureOnTop();
        map.on('styledata', ensureOnTop);
    };

    const refresh = async (cfg) => {
        const p = paintFrom(cfg);
        if (map.getLayer(HALO_LYR)) {
            map.setPaintProperty(HALO_LYR, 'line-color', p.haloColor);
            map.setPaintProperty(HALO_LYR, 'line-width', p.haloWidth);
            map.setPaintProperty(HALO_LYR, 'line-opacity', p.opacity);
        }
        if (map.getLayer(LINE_LYR)) {
            map.setPaintProperty(LINE_LYR, 'line-color', p.color);
            map.setPaintProperty(LINE_LYR, 'line-width', p.width);
            map.setPaintProperty(LINE_LYR, 'line-opacity', p.opacity);
        }
    };

    const unmount = () => {
        map.off('styledata', ensureOnTop);
        for (const id of [LINE_LYR, HALO_LYR]) if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(SRC)) map.removeSource(SRC);
    };

    return liveDataSync(map, {
        sectionKey: 'landmass', initialConfig: config,
        mount, refresh, unmount,
        refreshMs: 3600000,   // static vector tiles; config edits drive the refresh, not a timer
    });
}
