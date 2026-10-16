// The map half of the shared choropleth interaction (rules in _choropleth_inspector.js):
// a hover badge at the country's label point, and on click a popup anchored there with the
// badge kept and ringed while it is open. Used by country_stats.js and internet_outages.js,
// so every choropleth layer behaves the same.
//
// Call after the layer's fill layer exists. The layer supplies which countries have data
// (`hasData`), the popup HTML for one (`popupHtml`) and its badge look (`badgeSpec`:
// {fill, glyph}); this owns the badge source/layers, the popup, the mouse and click wiring
// and their teardown. Taps arrive as clicks, so touch screens need nothing extra.
import { createChoroplethInspector } from './_choropleth_inspector.js';

const BADGE_PX = 44;
const RING_RADIUS = 17;

export function badgeImageId(spec) {
    return `choropleth-badge-${spec.fill}-${spec.glyph || 'dot'}`;
}

// A light fill (yellow, white) would lose a white glyph, so the mark goes dark on those.
function glyphColor(fill) {
    const n = parseInt(fill.slice(1), 16);
    const luma = 0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
    return luma > 170 ? '#333333' : '#ffffff';
}

function drawBadge({ fill, glyph }) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = BADGE_PX;
    const ctx = canvas.getContext('2d');
    const c = BADGE_PX / 2;
    const r = c - 5;
    // A thin dark outer edge keeps the white outline visible over pale basemap and fills.
    ctx.beginPath(); ctx.arc(c, c, r + 2, 0, 2 * Math.PI);
    ctx.fillStyle = 'rgba(0,0,0,0.45)'; ctx.fill();
    ctx.beginPath(); ctx.arc(c, c, r, 0, 2 * Math.PI);
    ctx.fillStyle = fill; ctx.fill();
    ctx.lineWidth = 3; ctx.strokeStyle = '#ffffff'; ctx.stroke();
    if (glyph) {
        ctx.fillStyle = glyphColor(fill);
        ctx.font = 'bold 22px sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(glyph, c, c + 1);
    }
    return ctx.getImageData(0, 0, BADGE_PX, BADGE_PX);
}

// Label point and properties per country code, from the bundled polygons. Where several
// features share a code (Australia and its territories) the first one -- the mainland -- wins.
function indexCountries(geometry, codeProp) {
    const byCode = new Map();
    for (const f of geometry.features) {
        const code = f.properties[codeProp];
        if (code && !byCode.has(code)) byCode.set(code, f.properties);
    }
    return byCode;
}

export function attachChoroplethInspector(map, {
    id, fillLayerId, codeProp, geometry, hasData, popupHtml, badgeSpec,
}) {
    const sourceId = `${id}-badge-source`;
    const badgeLayerId = `${id}-badge`;
    const ringLayerId = `${id}-badge-ring`;
    const countries = indexCountries(geometry, codeProp);
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 15, maxWidth: '320px' });

    const labelOf = (code) => countries.get(code)?.label || null;

    const badgeFeature = (code, kind) => {
        const label = labelOf(code);
        if (!label) return null;
        const spec = badgeSpec(code);
        const image = badgeImageId(spec);
        if (!map.hasImage(image)) map.addImage(image, drawBadge(spec), { pixelRatio: 2 });
        return { type: 'Feature', geometry: { type: 'Point', coordinates: label }, properties: { kind, image } };
    };

    map.addSource(sourceId, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({
        id: badgeLayerId, type: 'symbol', source: sourceId,
        layout: { 'icon-image': ['get', 'image'], 'icon-allow-overlap': true, 'icon-ignore-placement': true },
    });
    map.addLayer({
        id: ringLayerId, type: 'circle', source: sourceId,
        filter: ['==', ['get', 'kind'], 'selected'],
        paint: {
            'circle-radius': RING_RADIUS, 'circle-color': 'rgba(0,0,0,0)',
            'circle-stroke-color': '#ffffff', 'circle-stroke-width': 3,
        },
    });

    const render = ({ hover, selected, anchor }) => {
        const features = [hover && badgeFeature(hover, 'hover'), selected && badgeFeature(selected, 'selected')]
            .filter(Boolean);
        map.getSource(sourceId)?.setData({ type: 'FeatureCollection', features });
        if (selected) {
            // A country with no label point still opens its popup, at the click.
            popup.setLngLat(labelOf(selected) || anchor).setHTML(popupHtml(selected, countries.get(selected))).addTo(map);
        } else {
            popup.remove();
        }
    };

    const inspector = createChoroplethInspector({ hasData, onChange: render });

    const codeUnder = (features) => (features && features.length ? features[0].properties[codeProp] : null);
    const onMove = (e) => {
        const code = codeUnder(e.features);
        inspector.enter(code);
        map.getCanvas().style.cursor = code && hasData(code) ? 'pointer' : '';
    };
    const onLeave = () => {
        inspector.leave();
        map.getCanvas().style.cursor = '';
    };
    const onClick = (e) => {
        const code = codeUnder(map.queryRenderedFeatures(e.point, { layers: [fillLayerId] }));
        inspector.click(code, e.lngLat);
    };
    map.on('mousemove', fillLayerId, onMove);
    map.on('mouseleave', fillLayerId, onLeave);
    map.on('click', onClick);

    return {
        dataChanged: () => inspector.dataChanged(),
        clear: () => inspector.clear(),
        destroy() {
            map.off('mousemove', fillLayerId, onMove);
            map.off('mouseleave', fillLayerId, onLeave);
            map.off('click', onClick);
            popup.remove();
            for (const lid of [ringLayerId, badgeLayerId]) {
                if (map.getLayer(lid)) map.removeLayer(lid);
            }
            if (map.getSource(sourceId)) map.removeSource(sourceId);
            map.getCanvas().style.cursor = '';
        },
    };
}
